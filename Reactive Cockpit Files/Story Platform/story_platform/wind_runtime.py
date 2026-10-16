"""Connect environment lifecycle to wind; never used by editor dry-run."""
import json
import math
import time
from .storage import atomic_write_json
from .wind import WindWorker, validate_wind, window_state, wind_clue

class WindRuntime:
    def __init__(self, app_dir, journal_dir, environments, printer=print):
        self.env=environments;self.print=printer
        self.path=app_dir/'runtime'/('wind_cycles_'+environments.graph.graph_id+'.json')
        self.status_path=app_dir/'runtime'/'wind_status.json'
        try:self.cycles=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.cycles={}
        if not isinstance(self.cycles,dict):self.cycles={}
        self.worker=WindWorker(app_dir/'wind_settings.json',journal_dir/'Status.json')
        self.last_status=0;self.selected=None;self.warning_modes=None
    def start(self):self.worker.start()
    def update(self, world, enabled=True, now=None):
        now=time.time() if now is None else now
        candidates=[]
        if enabled and self.env.running and self.env.outputs_enabled:
            for zone in self.env.zones:
                zs=self.env.state['zones'].get(str(zone['id']),{})
                if zs.get('phase')!='active' or zs.get('exit_pending_at') or not self.env._location_state(zone,world)['entry']:continue
                for layer in zone.get('layers',[]):
                    if world.get('vehicle') not in ('ship','fighter','on_foot','srv'):continue
                    if zone.get('active_vehicles') is not None and world['vehicle'] not in zone['active_vehicles']:continue
                    if layer.get('type')!='wind' or not zs.get('layers',{}).get(str(layer['id']),{}).get('playing'):continue
                    if not zone.get('location',{}).get('body'):continue
                    validate_wind(layer)
                    key=str(zone['id'])+'.'+str(layer['id'])
                    # Local circles override whole-body wind at equal priority.
                    radius=float(zone.get('location',{}).get('entry_radius_m',float('inf')))
                    candidates.append((float(layer.get('priority',0)),-radius,key,zone,layer))
        request=None;clue=''
        selected=max(candidates,key=lambda x:x[:3]) if candidates else None
        key=selected[2] if selected else None
        if self.warning_modes is not None and (world.get('vehicle') not in self.warning_modes or key!=self.selected):
            self.env._emit('wind','audio',{'action':'stop_layer','layer':'environment.wind.forecast'})
            self.warning_modes=None
        if self.selected and key!=self.selected:
            self.env._emit('wind','audio',{'action':'stop_layer','layer':'environment.wind.forecast'})
        self.selected=key
        if selected:
            _,_,key,zone,layer=selected
            cycle=self.cycles.get(key)
            signature=['normal-gap-v1',layer.get('mode','stable'),layer.get('interval_minutes',20),layer.get('duration_minutes',5)]
            try:
                valid_anchor=math.isfinite(float(cycle['anchor']))
            except (TypeError,ValueError,KeyError,OverflowError):
                valid_anchor=False
            if not valid_anchor or cycle.get('signature')!=signature:
                cycle={'anchor':now,'signature':signature,'warned':None};self.cycles[key]=cycle
                atomic_write_json(self.path,self.cycles)
            state=window_state(layer,float(cycle['anchor']),now)
            request={'key':key,'location':zone['location'],'wind_from':float(layer.get('wind_from',270)),'strength':state['strength'],'active_vehicles':zone.get('active_vehicles'),'vehicle':world.get('vehicle'),
                     'paused_by':self.pause_sources('environment.'+key,world)}
            warn=state['warning_cycle']
            # Only play during the warning period, never catch up old audio.
            if warn is not None and cycle.get('warned')!=warn and world.get('vehicle') in layer.get('warning_vehicles',['ship','fighter','srv','on_foot']):
                if layer.get('warning_asset'):
                    self.warning_modes=layer.get('warning_vehicles',['ship','fighter','srv','on_foot'])
                    self.env._emit(str(zone['id']),'audio',{'action':'play_layer','layer':'environment.wind.forecast',
                        'asset':layer['warning_asset'],'volume':float(layer.get('warning_volume',1)), 'loop':False})
                cycle['warned']=warn;atomic_write_json(self.path,self.cycles)
            if state['remaining'] is not None and world.get('vehicle') in ('ship','fighter','on_foot','srv'):
                clue=wind_clue(layer,state,str(zone.get('title',zone['id'])))
        self.worker.publish(request)
        if now-self.last_status>=.5:
            atomic_write_json(self.status_path,{'updated_at':now,'graph_id':self.env.graph.graph_id,
                'zone':key,'strength':request['strength'] if request else None,
                'clue':clue,'input_active':self.worker.active,'status':self.worker.message})
            self.last_status=now
    def pause_sources(self, target, world):
        sources=[]
        for zone in self.env.zones:
            state=self.env.state['zones'].get(str(zone['id']),{})
            if state.get('phase')!='active':continue
            allowed=zone.get('active_vehicles')
            if allowed is not None and world.get('vehicle') not in allowed:continue
            if any(a.get('action')=='pause_wind' and a.get('target_layer')==target
                   for a in zone.get('layer_adjustments',[]) if isinstance(a,dict)):
                sources.append(str(zone.get('title',zone['id'])))
        return sources
    def close(self):
        self.worker.close()
        atomic_write_json(self.status_path,{'updated_at':time.time(),'graph_id':self.env.graph.graph_id,'clue':'','input_active':False,'status':'Stopped'})
