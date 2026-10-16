"""Read active Elite keyboard assignments. Supports reviewed, backed-up alternative-key setup."""
import argparse, hashlib, json, os, re, sys, subprocess, tempfile, uuid
from pathlib import Path
import xml.etree.ElementTree as ET
from .storage import atomic_write_json
from .wind_input import resolve_key

ACTIONS = {
 'flight': [('forward','ForwardThrustButton','Thrust forward'),('backward','BackwardThrustButton','Thrust backward'),('left','LeftThrustButton','Thrust left'),('right','RightThrustButton','Thrust right')],
 'on_foot': [('forward','HumanoidForwardButton','Walk forward'),('backward','HumanoidBackwardButton','Walk backward'),('left','HumanoidStrafeLeftButton','Strafe left'),('right','HumanoidStrafeRightButton','Strafe right')],
 'srv': [('forward','IncreaseSpeedButtonMax','Accelerate button'),('backward','DecreaseSpeedButtonMax','Decelerate button'),('left','BuggyRollLeftButton','Roll left'),('right','BuggyRollRightButton','Roll right')],
}

def default_folder():
 return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'Frontier Developments/Elite Dangerous/Options/Bindings'

def shared_confirmation_path():
 return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'ReactiveCockpit'/'wind_confirmed_bindings.json'

def installation_scope(path):
 return os.path.normcase(str(Path(path).resolve()))

def binding_settings(path):
 try:settings=json.loads(Path(path).read_text(encoding='utf-8-sig'))
 except (OSError,ValueError):settings={}
 # Confirmation and deferral belong to this installation, never another copy.
 if settings.get('setup_installation') != installation_scope(path):
  settings.update(enabled=False,setup_deferred=False,binding_confirmation=None)
 return settings

def key_name(raw):
 key=raw.removeprefix('Key_').upper()
 aliases={'LEFTARROW':'LEFT','RIGHTARROW':'RIGHT','UPARROW':'UP','DOWNARROW':'DOWN','RETURN':'ENTER','SPACE':'SPACE','BACKSPACE':'BACKSPACE','DELETE':'DELETE','INSERT':'INSERT','HOME':'HOME','END':'END','PAGEUP':'PAGEUP','PAGEDOWN':'PAGEDOWN'}
 if re.fullmatch('NUMPAD_?[0-9]',key):key='NUM'+key[-1]
 key=aliases.get(key,key)
 resolve_key(key)
 return key

def elite_key_name(key):
 if re.fullmatch('NUM[0-9]',key):return 'Key_Numpad_'+key[-1]
 return 'Key_'+key

def choices(root,tag,slots=('Primary','Secondary')):
 node=root.find(tag)
 if node is None:return [],'Please assign a key for this in Elite.'
 result=[];unsupported=False
 for slot in slots:
  b=node.find(slot)
  if b is None or b.get('Device')!='Keyboard':continue
  if b.findall('.//Modifier'):unsupported=True;continue
  try:k=key_name(b.get('Key',''))
  except ValueError:unsupported=True;continue
  if k not in result:result.append(k)
 return result,('Assign a plain keyboard key (no modifiers) in Elite.' if unsupported else 'Please assign a key for this in Elite.')

def read_active(folder):
 folder=Path(folder);start=folder/'StartPreset.4.start'
 if not start.exists():start=folder/'StartPreset.start'
 if not start.exists():raise ValueError('Elite active presets not found. Open Elite, apply your controls, then Re-read bindings. Check the bindings folder below.')
 lines=start.read_text(encoding='utf-8-sig').splitlines()
 lines=[s.strip() for s in lines]
 while lines and not lines[-1]:lines.pop()
 if len(lines) not in (1,4) or any(not s for s in lines):raise ValueError('Unrecognised active preset list; expected one or four preset names.')
 names={m:lines[i] if len(lines)==4 else lines[0] for m,i in [('general',0),('flight',1),('srv',2),('on_foot',3)]}
 roots={};sources={}
 for mode,name in names.items():
  if Path(name).name!=name or any(c in name for c in '/\\:'):raise ValueError('Invalid preset name.')
  found=[]
  for p in folder.glob('*.binds'):
   match=re.fullmatch(re.escape(name)+r'\.(\d+)\.(\d+)\.binds',p.name,re.I)
   if match:found.append((tuple(map(int,match.groups())),p))
   elif p.name.casefold()==(name+'.binds').casefold():found.append(((0,0),p))
  if not found:raise ValueError('Active '+mode+' preset "'+name+'" is not saved here. In Elite add/apply a secondary keyboard assignment to save a Custom preset, then Re-read bindings.')
  p=max(found,key=lambda x:x[0])[1]
  roots[mode]=ET.fromstring(p.read_bytes());sources[mode]=str(p.resolve())
 return roots,sources

_ALLOWED={d:{tag for actions in ACTIONS.values() for direction,tag,_ in actions if direction==d}|{tag+'_Landing' for direction,tag,_ in ACTIONS['flight'] if direction==d} for d in ('forward','backward','left','right')}
_CANDIDATES=list('7890654321PLOKIJUHNMVBCXZ')+['NUM'+str(i) for i in range(10)]
_RESERVED={'W','A','S','D','Q','E','SPACE','ENTER','TAB','UP','DOWN','LEFT','RIGHT','BACKSPACE'}

def _digest(folder,sources):
 paths=set(sources.values())
 for name in ('StartPreset.4.start','StartPreset.start'):
  p=Path(folder)/name
  if p.exists():paths.add(str(p.resolve()))
 return {p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sorted(paths)}

def _uses(roots):
 uses={}
 for root in roots.values():
  for action in root:
   for e in action.iter():
    if e.get('Device')=='Keyboard':
     try:key=key_name(e.get('Key',''))
     except ValueError:continue
     uses.setdefault(key,set()).add(action.tag)
 return uses

# Profiles contain stale copies of every mode. Only the selected mode's
# assignments are relevant when reusing a key. New assignments remain globally
# conservative, so a newly chosen key does not steal an existing control.
_SRV_TAGS={'ToggleDriveAssist','SteeringAxis','SteerLeftButton','SteerRightButton',
 'VerticalThrustersButton','DriveSpeedAxis','IncreaseSpeedButtonMax',
 'DecreaseSpeedButtonMax','IncreaseSpeedButtonPartial','DecreaseSpeedButtonPartial',
 'RecallDismissShip','EnableMenuGroupsSRV'}
_COMMON_TAGS={'Pause','UIFocus','CycleNextPanel','CyclePreviousPanel',
 'CycleNextPage','CyclePreviousPage','FriendsMenu','HMDReset','MicrophoneMute'}
def _context(tag):
 if 'Humanoid' in tag or tag in {'EnableMenuGroupsOnFoot','EnableAimAssistOnFoot'}:return 'on_foot'
 if 'Buggy' in tag or tag in _SRV_TAGS or tag.startswith(('MouseTurret','MouseBuggy')):return 'srv'
 # Camera movement is a separate control context, not normal piloting.
 if ('Cam' in tag or tag.startswith(('FStop','Vanity','CommanderCreator','Store','Exploration','SAA','FSS'))
     or tag in {'ToggleRotationLock','FixCameraRelativeToggle','FixCameraWorldToggle','ToggleAdvanceMode'}):return 'camera'
 if tag.startswith(('UI_','GalnetAudio','HeadLook','Headlook')) or tag in _COMMON_TAGS:return 'general'
 return 'flight'

def _active_uses(roots,mode):
 uses={}
 for source in (mode,'general'):
  for action in roots[source]:
   context=_context(action.tag)
   if context != source:continue
   for e in action.iter():
    if e.get('Device')=='Keyboard':
     try:key=key_name(e.get('Key',''))
     except ValueError:continue
     uses.setdefault(key,set()).add(action.tag)
 return uses

def _safe(key,direction,uses):
 return bool(key and key not in _RESERVED and uses.get(key,set())<=_ALLOWED[direction])

def _change(result,path,node,tag,key):
 old=node.find('Secondary');previous=ET.tostring(old,encoding='unicode') if old is not None else None
 replacement=bool(old is not None and old.get('Device') not in (None,'','{NoDevice}'))
 result['changes'].append({'path':path,'tag':tag,'key':key,'previous':previous,'replacement':replacement})
 oldlabel=(old.get('Device','')+' / '+old.get('Key','')) if old is not None else ''
 return key+(' (replace '+oldlabel+')' if replacement else ' (add alternative)')

def _scan(folder,settings_path,propose=False):
 r={'ready':False,'confirmed':False,'rows':[],'folder':str(folder),'errors':[],'warnings':[],'fingerprint':'','sources':{},'changes':[]}
 try:
  roots,sources=read_active(folder);r['sources']=sources;r['snapshot']=_digest(folder,sources)
  uses=_uses(roots);groups={};preferred={}
  for mode,actions in ACTIONS.items():
   groups[mode]={}
   for direction,tag,label in actions:
    node=roots[mode].find(tag)
    slots=('Secondary',)
    keys,_=choices(roots[mode],tag,slots)
    # Player-selected secondary keys are authoritative; conflicts do not veto them.
    key=next((k for k in keys if re.fullmatch(r'[A-Z0-9]|NUM[0-9]',k)),None)
    secondary=node.find('Secondary') if node is not None else None
    occupied=secondary is not None and secondary.get('Device') not in (None,'','{NoDevice}')
    display=key
    if not key and propose and not occupied:
     if node is None:r['errors'].append(mode+': '+label+' is missing. Apply this control in Elite first.')
     else:
      candidates=([preferred[direction]] if direction in preferred else [])+_CANDIDATES
      key=next((k for k in candidates if _safe(k,direction,uses)),None)
      if key:
       display=_change(r,sources[mode],node,tag,key);uses.setdefault(key,set()).add(tag)
    if key:groups[mode][direction]=key;preferred.setdefault(direction,key)
    r['rows'].append({'mode':mode,'action':label,'tag':tag,'direction':direction,'key':key,'display':display or ('Set a secondary letter, number or numpad digit in Elite, then Read Again.' if occupied else 'Assign a secondary key of your choice in Elite, then Read Again.'),'ok':bool(key),'source':Path(sources[mode]).name})
  r['bindings_by_vehicle']=groups
  r['fingerprint']=hashlib.sha256(json.dumps({'schema':6,'installation':installation_scope(settings_path),'snapshot':r['snapshot'],'groups':groups,'changes':r['changes']},sort_keys=True).encode()).hexdigest()
  r['ready']=all(row['ok'] for row in r['rows']) and not r['errors']
  saved=binding_settings(settings_path)
  r['confirmed']=bool(not saved.get('setup_deferred',False) and r['ready'] and not r['changes'] and saved.get('binding_confirmation')==r['fingerprint'] and saved.get('bindings_by_vehicle')==groups)
  if any(c['replacement'] for c in r['changes']):r['warnings'].append('Existing alternatives will be replaced as shown. Primary controls stay unchanged. Confirm only if you accept these replacements.')
  if r['changes']:r['warnings'].append('Close Elite before confirming. Original profiles will be backed up. Start Elite after setup completes.')
 except (OSError,ValueError,ET.ParseError) as exc:r['errors'].append(str(exc))
 return r

def inspect(folder,settings_path):return _scan(folder,settings_path)
def review(folder,settings_path):
 r=inspect(folder,settings_path)
 return r if r['confirmed'] else _scan(folder,settings_path,True)

def elite_running():
 if os.name!='nt':raise ValueError('Automatic control setup requires Windows.')
 p=subprocess.run(['tasklist','/FO','CSV','/NH'],capture_output=True,text=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 if p.returncode:raise ValueError('Could not check whether Elite is closed. No bindings changed.')
 return any(name in p.stdout.lower() for name in ('elitedangerous64.exe','elitedangerous.exe'))

def _write_bytes(path,data):
 path=Path(path);fd,tmp=tempfile.mkstemp(prefix='.rc-controls-',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def apply_review(folder,settings_path,expected):
 r=review(folder,settings_path)
 if not r['ready'] or r['fingerprint']!=expected:raise ValueError('Controls changed or are incomplete. Re-read before confirming.')
 if not r['changes']:return confirm(folder,settings_path,r['fingerprint'])
 if elite_running():raise ValueError('Close Elite Dangerous before setting up controls, then confirm again.')
 originals={c['path']:Path(c['path']).read_bytes() for c in r['changes']};outputs={}
 for path,raw in originals.items():
  text=raw.decode('utf-8-sig')
  for c in (c for c in r['changes'] if c['path']==path):
   pattern=r'(<'+re.escape(c['tag'])+r'\b[^>]*>)(.*?)(</'+re.escape(c['tag'])+r'>)'
   matches=list(re.finditer(pattern,text,re.S))
   if len(matches)!=1:raise ValueError('Cannot safely locate '+c['tag']+' in preset.')
   m=matches[0];body=m.group(2);replacement='<Secondary Device="Keyboard" Key="'+elite_key_name(c['key'])+'" />'
   pattern=r'<Secondary\b[^>]*(?:/>|>.*?</Secondary\s*>)'
   if len(re.findall(pattern,body,re.S))>1:raise ValueError('Ambiguous alternative binding.')
   if re.search(pattern,body,re.S):body=re.sub(pattern,lambda _:replacement,body,count=1,flags=re.S)
   else:body+='\n    '+replacement+'\n'
   text=text[:m.start()]+m.group(1)+body+m.group(3)+text[m.end():]
  out=text.encode('utf-8');out=b'\xef\xbb\xbf'+out if raw.startswith(b'\xef\xbb\xbf') else out
  ET.fromstring(out);outputs[path]=out
 backup=shared_confirmation_path().parent/'control_backups'/uuid.uuid4().hex;backup.mkdir(parents=True,exist_ok=False)
 for i,(path,raw) in enumerate(originals.items()):(backup/(str(i)+'_'+Path(path).name)).write_bytes(raw)
 atomic_write_json(backup/'restore-map.json',{'files':list(originals),'snapshot':r['snapshot']})
 written=[]
 try:
  if _digest(folder,r['sources'])!=r['snapshot']:raise ValueError('Controls changed during setup. Re-read and retry.')
  if elite_running():raise ValueError('Elite started during setup. Close it and retry.')
  for path,out in outputs.items():
   if Path(path).read_bytes()!=originals[path]:raise ValueError('A control profile changed during setup.')
   _write_bytes(path,out);written.append(path)
  checked=inspect(folder,settings_path)
  if not checked['ready'] or checked['bindings_by_vehicle']!=r['bindings_by_vehicle']:raise ValueError('Control verification failed. Original profiles restored.')
  result=confirm(folder,settings_path,checked['fingerprint']);result['backup']=str(backup);return result
 except Exception:
  for path in reversed(written):
   if Path(path).read_bytes()==outputs[path]:_write_bytes(path,originals[path])
  raise

def confirm(folder,settings_path,expected):
 data=inspect(folder,settings_path)
 if not data['ready']:raise ValueError('Assign the missing supported secondary keys in Elite, apply, then Read Again.')
 if expected!=data['fingerprint']:raise ValueError('Bindings changed since review. Re-read and confirm the new list.')
 p=Path(settings_path)
 try:s=json.loads(p.read_text(encoding='utf-8-sig'))
 except (OSError,ValueError):s={}
 s.update(enabled=True,setup_deferred=False,setup_installation=installation_scope(p),method='scan',bindings=data['bindings_by_vehicle']['flight'],bindings_by_vehicle=data['bindings_by_vehicle'],binding_confirmation=data['fingerprint'],bindings_folder=str(Path(folder).resolve()))
 atomic_write_json(p,s)
 return inspect(folder,p)

def defer_setup(folder,settings_path):
 # Do not read or edit Elite profiles: skip must work even when detection fails.
 p=Path(settings_path)
 try:s=json.loads(p.read_text(encoding='utf-8-sig'))
 except (OSError,ValueError):s={}
 s.update(enabled=False,setup_deferred=True,setup_installation=installation_scope(p))
 atomic_write_json(p,s)
 return {'deferred':True,'confirmed':False}

def reset_setup(folder,settings_path):
 p=Path(settings_path)
 try:settings=json.loads(p.read_text(encoding='utf-8-sig'))
 except (OSError,ValueError):settings={}
 settings.update(enabled=False,setup_deferred=False,binding_confirmation=None,
                 setup_installation=installation_scope(p))
 atomic_write_json(p,settings)
 return {'confirmed':False,'deferred':False}

def activation_status(folder,settings_path):
 if binding_settings(settings_path).get('setup_deferred',False):
  return {'deferred':True,'confirmed':False}
 result=inspect(folder,settings_path)
 return {'deferred':False,'confirmed':result['confirmed']}

def main():
 a=argparse.ArgumentParser();a.add_argument('command',choices=['inspect','review','confirm','apply','defer','activation','reset']);a.add_argument('--settings',required=True);a.add_argument('--folder');a.add_argument('--expected');args=a.parse_args()
 folder=args.folder
 if not folder:
  try:folder=binding_settings(args.settings).get('bindings_folder')
  except (OSError,ValueError):pass
 folder=folder or str(default_folder())
 try:
  data=({'confirm':confirm,'apply':apply_review}[args.command](folder,args.settings,args.expected) if args.command in ('confirm','apply') else {'inspect':inspect,'review':review,'defer':defer_setup,'activation':activation_status,'reset':reset_setup}[args.command](folder,args.settings))
  print(json.dumps(data,ensure_ascii=True))
 except Exception as exc:print(json.dumps({'error':str(exc)}));sys.exit(1)
if __name__=='__main__':main()
