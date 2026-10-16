"""Ship, SRV and on-foot environment wind. Pure timings plus a guarded 50 Hz input worker."""
from __future__ import annotations
import copy
import json
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from .wind_input import WIND, WindowsInput, resolve_key

SIDE_PATTERN = tuple(WIND[:-1]) + ((WIND[-1][0], .25),)

def finite(value, label):
    if isinstance(value, bool): raise ValueError(label + ' must be a number')
    v = float(value)
    if not math.isfinite(v): raise ValueError(label + ' must be finite')
    return v

def validate_wind(layer):
    modes = layer.get('warning_vehicles', ['ship','fighter','srv','on_foot'])
    if not isinstance(modes,list) or any(m not in ('ship','fighter','srv','on_foot') for m in modes):
        raise ValueError('Advance audio vehicles must be ship, fighter, srv or on_foot')
    for key in ('strength', 'changed_strength'):
        v = finite(layer.get(key, 85), key)
        if not 40 <= v <= 100: raise ValueError(key + ' must be between 40 and 100')
    if not 0 <= finite(layer.get('wind_from', 270), 'Wind bearing') < 360:
        raise ValueError('Wind FROM bearing must be 0 to less than 360')
    if layer.get('mode', 'stable') not in ('stable', 'dynamic'):
        raise ValueError('Wind mode must be stable or dynamic')
    if layer.get('mode') == 'dynamic':
        interval = finite(layer.get('interval_minutes', 20), 'Interval') * 60
        duration = finite(layer.get('duration_minutes', 5), 'Duration') * 60
        if interval <= 20 or duration <= 0:
            raise ValueError('Interval must exceed 20 seconds; duration must be positive')
    v = finite(layer.get('warning_volume', 1), 'Warning volume')
    if not 0 <= v <= 1: raise ValueError('Warning volume must be 0 to 1')
    finite(layer.get('priority', 0), 'Priority')
    for key in ('normal_message', 'changed_message'):
        if key in layer and not isinstance(layer[key], str):
            raise ValueError(key + ' must be text')
    asset = layer.get('warning_asset', '')
    if not isinstance(asset, str): raise ValueError('Advance audio must be a relative asset path')
    if asset and (asset.startswith(('/', '\\')) or ':' in asset or '..' in asset.replace('\\', '/').split('/')):
        raise ValueError('Choose advance audio inside the story media folder')

def fore_timing(strength):
    """Exact v4 short/regular construction; 85 reproduces approved Strong."""
    f = finite(strength, 'Strength') / 100
    if not .4 <= f <= 1: raise ValueError('Strength must be 40 to 100')
    h = sum(a*.2 + f*(a*.5-a*.2) for a,g in WIND)/len(WIND)*.45
    g = sum(max(1.3,b*7) + f*(max(.55,b*3)-max(1.3,b*7)) for a,b in WIND)/len(WIND)*.45
    return h,g

def window_state(layer, anchor, now):
    if layer.get('mode', 'stable') == 'stable':
        return {'strength': float(layer.get('strength',85)), 'changed': False, 'remaining': None, 'warning_cycle': None}
    interval = float(layer.get('interval_minutes',20))*60
    duration = float(layer.get('duration_minutes',5))*60
    elapsed = max(0, now-anchor)
    period = interval + duration
    cycle = int(elapsed//period)
    phase = elapsed-cycle*period
    changed = phase >= interval
    remaining = period-phase if changed else interval-phase
    warning = cycle+1 if not changed and 0 < remaining <= 20 else None
    return {'strength': float(layer.get('changed_strength',85) if changed else layer.get('strength',85)),
            'changed': changed, 'remaining': remaining, 'warning_cycle': warning}

def wind_clue(layer, state, title):
    if state['remaining'] is None:
        return ''
    seconds = max(0, math.ceil(state['remaining']))
    timer = f'{seconds//60:02d}:{seconds%60:02d}'
    key = 'changed_message' if state['changed'] else 'normal_message'
    fallback = (f'{title}: Wind change ACTIVE — {{countdown}} remaining.' if state['changed']
                else f'{title}: Next wind change in {{countdown}}.')
    return layer.get(key, fallback).replace('{countdown}', timer)

class GustClock:
    def __init__(self, pattern): self.pattern=pattern; self.reset()
    def reset(self): self.start=None; self.index=0
    def sample(self, now):
        if self.start is None: self.start=now
        h,g=self.pattern[self.index]
        if now-self.start >= h+g:
            self.index=(self.index+1)%len(self.pattern);self.start=now;h,g=self.pattern[self.index]
        return now-self.start,h

def actions(bearing, heading, side_phase, side_hold, fore_phase, fore_hold):
    a=math.radians((bearing+180-heading)%360);right,forward=math.sin(a),math.cos(a)
    wanted=set()
    if abs(right)>.05 and side_phase<side_hold*abs(right): wanted.add('right' if right>0 else 'left')
    if abs(forward)>.05 and fore_phase<fore_hold*abs(forward): wanted.add('forward' if forward>0 else 'backward')
    return wanted

def wind_actions(strength, bearing, heading, side_phase, side_hold, fore_phase, fore_hold):
    # 100 is the original shared-clock Strong profile, not an interpolation point.
    if strength == 100:
        fore_phase, fore_hold = side_phase, side_hold
    return actions(bearing, heading, side_phase, side_hold, fore_phase, fore_hold)

def status_guard(s, location, armed_at, now, active_vehicles=None, expected_vehicle=None):
    try:
        stamp=datetime.fromisoformat(s['timestamp'].replace('Z','+00:00')).timestamp()
        if s.get('event')!='Status' or stamp<=armed_at or not -2<=now-stamp<=15: return 'Waiting for fresh flight telemetry'
        f=int(s['Flags']);f2=int(s.get('Flags2',0))
        if not f2&1 and f&(1<<26) and f&((1<<24)|(1<<25)):return 'Waiting for vehicle handover'
        vehicle = 'on_foot' if f2&1 else 'fighter' if f&(1<<25) else 'srv' if f&(1<<26) else 'ship' if f&(1<<24) else None
        on_foot = vehicle == 'on_foot'
        ground = vehicle in ('on_foot','srv')
        if expected_vehicle is not None and vehicle != expected_vehicle: return 'Waiting for vehicle handover'
        if active_vehicles is not None and vehicle not in active_vehicles: return 'Vehicle excluded by wind zone'
        if on_foot:
            if not f2&(1<<4) or f2&((1<<3)|(1<<13)|2|4):return 'On foot outside on a planet required'
        elif vehicle == 'srv':
            if not f&(1<<21):return 'SRV surface coordinates required'
            if f&((1<<12)|(1<<13)):return 'SRV handbrake or turret view'
        else:
            if not f&(1<<21) or vehicle is None or f&(1<<26): return 'Ship or fighter on planetary surface required'
            if f&(1|2|(1<<4)|(1<<17)|(1<<30)) or f2&(1|2|4|(1<<12)|(1<<18)) or (f2&(1<<17) and vehicle != 'fighter'):
                return 'Flight state paused'
        gui_focus = int(s.get('GuiFocus',0 if ground else -1))
        cockpit_panel = vehicle in ('ship','fighter','srv') and gui_focus in (1,2,3,4)
        if gui_focus != 0 and not cockpit_panel:return 'Panel/map open'
        if not location.get('body') or str(s.get('BodyName','')).casefold()!=str(location['body']).casefold():return 'Outside wind body'
        head=finite(s['Heading'],'Heading')
        lat=finite(s['Latitude'],'Latitude');lon=finite(s['Longitude'],'Longitude')
        if not (-180 if ground else 0)<=head<=360 or not -90<=lat<=90 or not -180<=lon<=180:return 'Invalid coordinates'
        # On-foot Status can omit altitude. Match the environment system's ground-location policy.
        if 'Altitude' in s or not ground:
            alt=finite(s['Altitude'],'Altitude')
            if location.get('min_altitude_m') is not None and alt<float(location['min_altitude_m']):return 'Below zone'
            if location.get('max_altitude_m') is not None and alt>float(location['max_altitude_m']):return 'Above zone'
        if 'latitude' in location and 'longitude' in location:
            rad=finite(s.get('PlanetRadius',location.get('body_radius_m',0)),'Planet radius')
            if rad<=0:return 'Planet radius unavailable'
            a,b=math.radians(lat),math.radians(float(location['latitude']))
            d=math.sin((b-a)/2)**2+math.cos(a)*math.cos(b)*math.sin(math.radians(float(location['longitude'])-lon)/2)**2
            dist=2*rad*math.asin(min(1,math.sqrt(max(0,d))))
            if dist>float(location['entry_radius_m']):return 'Outside wind radius'
        return ''
    except (KeyError,ValueError,TypeError,OverflowError):return 'Invalid flight telemetry'

class Output:
    def __init__(self, bindings, method, factory=WindowsInput):
        self.keys={k:factory(v,method) for k,v in bindings.items()};self.active=set();self.allow_shared=False
    def update(self, wanted):
        for k in sorted(self.active-wanted):self.keys[k].send(False);self.active.remove(k)
        for k in sorted(wanted-self.active):
            if not self.allow_shared and self.keys[k].key_busy():raise RuntimeError('Wind key already held; restart Story Mode after releasing it')
            self.active.add(k);self.keys[k].send(True)
    def release(self):
        errors=[]
        for k in list(self.active):
            try:self.keys[k].send(False);self.active.remove(k)
            except Exception as e:errors.append(str(e))
        if errors:raise RuntimeError('; '.join(errors))

class WindWorker:
    """Owns keys on one thread. Heartbeat expiry releases if runtime stalls."""
    def __init__(self, settings_path, status_path):
        self.settings_path=Path(settings_path);self.status_path=Path(status_path)
        self.lock=threading.Lock();self.request=None;self.heartbeat=0
        self.quit=threading.Event();self.thread=None;self.message='Wind disabled';self.active=False
        self.armed_at=time.time();self.failed=False
    def start(self):
        if os.name!='nt':return
        self.thread=threading.Thread(target=self._run,name='planetary-wind',daemon=True);self.thread.start()
    def publish(self, request):
        with self.lock:self.request=copy.deepcopy(request);self.heartbeat=time.monotonic()
    def close(self):
        self.quit.set()
        if self.thread:self.thread.join(timeout=3)
    def _run(self):
        out=None
        try:
            settings=json.loads(self.settings_path.read_text(encoding='utf-8'))
            if settings.get('enabled') is not True:return
            from .wind_bindings import inspect, default_folder, binding_settings
            settings=binding_settings(self.settings_path)
            folder=settings.get('bindings_folder') or default_folder()
            checked=inspect(folder,self.settings_path)
            if not checked['confirmed']:
                self.message='Wind paused: confirm keybinds in the launcher';return
            groups=settings['bindings_by_vehicle']
            for group in groups.values():validate_bindings(group)
            bindings=groups['flight']
            next_binding_check=0
            method=settings.get('method','scan')
            if method not in ('scan','legacy'):raise ValueError('Choose scan or legacy')
            out=Output(bindings,method);out.allow_shared=True;api=next(iter(out.keys.values()));output_mode='flight'
            side=GustClock(SIDE_PATTERN);fore=GustClock((fore_timing(85),));last=None;resume_at=time.monotonic()+10
            while not self.quit.wait(.02):
                now=time.monotonic()
                with self.lock:req=copy.deepcopy(self.request);heartbeat=self.heartbeat
                reason=''
                if now>=next_binding_check:
                    checked=inspect(folder,self.settings_path);next_binding_check=now+2
                    if not checked['confirmed'] or checked.get('bindings_by_vehicle')!=groups:
                        out.release();self.message='Wind paused: bindings changed; stop and reconfirm in launcher';return
                focused=api.focused()
                try:
                    s=json.loads(self.status_path.read_text(encoding='utf-8-sig'))
                except (OSError,ValueError,TypeError):
                    s=None
                modifier_held=(api.modifiers() if not req or req.get('vehicle') not in ('ship','fighter','srv')
                               else any(api.pressed(vk) for vk in (0x11,0x12,0x5B,0x5C)))
                if not focused or modifier_held:
                    reason='Paused: Elite focus/modifier';resume_at=now+2
                elif now<resume_at:reason='Wind arming'
                elif now-heartbeat>1.5 or not req:reason='No active wind zone'
                elif req.get('paused_by'):reason='Wind sheltered by: '+', '.join(req['paused_by'])
                else:
                    try:
                        if s is None:raise ValueError('Status unavailable')
                        if not -2<=time.time()-self.status_path.stat().st_mtime<=15:reason='Stale Status file'
                        else:reason=status_guard(s,req['location'],self.armed_at,time.time(),req.get('active_vehicles'),req.get('vehicle'))
                    except (OSError,ValueError):reason='Waiting for Status file'
                if reason:
                    out.release();side.reset();fore.reset();self.active=False;self.message=reason;last=None;continue
                mode=req.get('vehicle') if req.get('vehicle') in ('on_foot','srv') else 'flight'
                if mode!=output_mode:
                    out.release()
                    out=Output(groups[mode],method)
                    out.allow_shared=True;output_mode=mode
                    api=next(iter(out.keys.values()));last=None
                identity=(req['key'],req['strength'],mode)
                if identity!=last:
                    out.release();fore=GustClock((fore_timing(req['strength']),))
                    if last is None or last[0]!=identity[0]:side.reset()
                    last=identity
                sp,sh=side.sample(now)
                # Original Strong test used one shared irregular clock for all axes.
                fp,fh=(sp,sh) if req['strength'] == 100 else fore.sample(now)
                out.update(wind_actions(req['strength'],req['wind_from'],float(s['Heading']) % 360,sp,sh,fp,fh))
                self.active=True;self.message='Active: '+req['key']
        except FileNotFoundError:self.message='Wind disabled: run WIND SETTINGS first'
        except Exception as exc:self.failed=True;self.message='Wind stopped: '+str(exc);print('[WIND]',self.message,flush=True)
        finally:
            self.active=False
            if out:
                for _ in range(3):
                    try:out.release();break
                    except Exception as exc:print('[WIND] Release failed:',exc,flush=True);time.sleep(.02)

def validate_bindings(bindings):
    if not isinstance(bindings,dict) or set(bindings)!={'left','right','forward','backward'}:raise ValueError('Four thrust bindings required')
    for key in bindings.values():
        if not isinstance(key,str):raise ValueError('Keyboard binding must be text')
        resolve_key(key)
    if len(set(bindings.values()))!=4:raise ValueError('Each thrust direction needs a different key')
