"""Wind authoring and explicit, machine-local input opt-in."""
import copy
import json
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from .config import APP_DIR, load_config, story_media_dir
from .storage import atomic_write_json
from .wind import validate_wind, validate_bindings, fore_timing

class WindLayerDialog(tk.Toplevel):
    def __init__(self, parent, initial=None):
        super().__init__(parent);self.title('Surface wind — ships, fighters, SRV and on foot');self.result=None
        self.transient(parent);self.grab_set();self.resizable(False,False)
        self.initial=copy.deepcopy(initial or {})
        defaults={'id':'wind','wind_from':270,'strength':85,'mode':'stable','interval_minutes':20,
                  'duration_minutes':5,'changed_strength':85,'warning_asset':'','warning_volume':1,'priority':0,
                  'normal_message':'Next wind change in {countdown}.',
                  'changed_message':'Wind change ACTIVE — {countdown} remaining.'}
        self.vars={k:tk.StringVar(value=str(self.initial.get(k,v))) for k,v in defaults.items()}
        f=ttk.Frame(self,padding=18);f.pack(fill='both',expand=True)
        labels=[('id','Layer ID'),('wind_from','Wind FROM bearing (0=N, 90=E)'),('strength','Normal fore/aft strength (40–100%)'),
                ('mode','Wind mode'),('interval_minutes','Normal wind between changes (minutes)'),('duration_minutes','Changed wind duration (minutes)'),
                ('changed_strength','Fore/aft strength during change (40–100%)'),('warning_asset','Advance audio (optional)'),
                ('warning_volume','Advance audio volume (0–1)'),('priority','Overlap priority (higher wins)'),
                ('normal_message','Normal wind message'),('changed_message','Changed wind message')]
        self.dynamic_widgets=[]
        for row,(key,label) in enumerate(labels):
            ttk.Label(f,text=label).grid(row=row,column=0,sticky='w',pady=5)
            if key=='mode':entry=ttk.Combobox(f,textvariable=self.vars[key],values=['stable','dynamic'],state='readonly',width=32)
            elif key in ('strength','changed_strength'):entry=ttk.Spinbox(f,textvariable=self.vars[key],from_=40,to=100,increment=1,width=32)
            else:entry=ttk.Entry(f,textvariable=self.vars[key],width=60 if key.endswith('_message') else 35)
            entry.grid(row=row,column=1,sticky='ew',padx=8)
            if key in ('interval_minutes','duration_minutes','changed_strength','warning_asset','warning_volume','normal_message','changed_message'):self.dynamic_widgets.append(entry)
        browse=ttk.Button(f,text='Browse…',command=self.browse);browse.grid(row=7,column=2);self.dynamic_widgets.append(browse)
        self.warning_vehicles={mode:tk.BooleanVar(value=mode in self.initial.get('warning_vehicles',['ship','fighter','srv','on_foot'])) for mode in ('ship','fighter','srv','on_foot')}
        audio_modes=ttk.Frame(f);audio_modes.grid(row=12,column=0,columnspan=3,sticky='w',pady=5)
        ttk.Label(audio_modes,text='Advance audio plays in:').pack(side='left')
        for mode,label in [('ship','Ship'),('fighter','Fighter'),('srv','SRV'),('on_foot','On foot')]:
            check=ttk.Checkbutton(audio_modes,text=label,variable=self.warning_vehicles[mode]);check.pack(side='left',padx=6);self.dynamic_widgets.append(check)
        ttk.Label(f,text='Sideways gusts stay at the tested Strong setting. 85% preserves the tested\nshort, regular fore/aft pulses. 100% uses original extreme gusts. Wind operates in ship/fighter flight, SRV and on foot.\nDynamic audio plays 20 seconds before each change starts.\nExample: 20 / 5 starts at minute 20, ends at 25; next starts at 45.\nUse {countdown} in either message for the live timer. Leave blank to show the story clue.\nEnable SRV / On foot in the zone vehicle checkboxes. Confirm keys in launcher.\nThe clock survives zone exit and restart. Changing interval/duration resets it.',wraplength=640).grid(row=13,column=0,columnspan=3,sticky='w',pady=12)
        ttk.Button(f,text='Player wind bindings…',command=lambda:WindSettingsDialog(self)).grid(row=14,column=0,sticky='w')
        ttk.Button(f,text='Cancel',command=self.destroy).grid(row=14,column=1,sticky='w')
        ttk.Button(f,text='Use wind layer',command=self.accept).grid(row=14,column=1,sticky='e')
        self.vars['mode'].trace_add('write',lambda *_:self.refresh());self.refresh()
    def refresh(self):
        for w in self.dynamic_widgets:w.configure(state='normal' if self.vars['mode'].get()=='dynamic' else 'disabled')
    def browse(self):
        root=story_media_dir(load_config())
        if not root or not Path(root).is_dir():
            messagebox.showerror('Audio folder','Set your story media folder through the existing sound-layer browser first.',parent=self);return
        root=Path(root).resolve();name=filedialog.askopenfilename(parent=self,initialdir=root,filetypes=[('Audio','*.mp3 *.wav *.ogg *.flac *.m4a'),('All files','*.*')])
        if name:
            p=Path(name).resolve()
            if not p.is_relative_to(root):messagebox.showerror('Audio folder','Choose a file inside '+str(root),parent=self);return
            self.vars['warning_asset'].set(p.relative_to(root).as_posix())
    def accept(self):
        try:
            layer=copy.deepcopy(self.initial);layer.update({k:v.get().strip() for k,v in self.vars.items()});layer['type']='wind'
            layer['id']=re.sub(r'[^a-zA-Z0-9_]+','_',layer['id']).strip('_').lower()
            if not layer['id']:raise ValueError('Enter a layer ID')
            for k in ('wind_from','strength','interval_minutes','duration_minutes','changed_strength','warning_volume','priority'):layer[k]=float(layer[k])
            layer['warning_vehicles']=[mode for mode,value in self.warning_vehicles.items() if value.get()]
            validate_wind(layer)
            for k in ('depth_response','start_delay_seconds','stop_delay_seconds'):layer.pop(k,None)
            self.result=layer;self.destroy()
        except (ValueError,TypeError) as e:messagebox.showerror('Invalid wind',str(e),parent=self)

class WindSettingsDialog(tk.Toplevel):
    def __init__(self,parent):
        super().__init__(parent);self.title('Player wind bindings');self.transient(parent);self.grab_set()
        self.path=APP_DIR/'wind_settings.json'
        try:settings=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):settings={}
        if not isinstance(settings,dict):settings={}
        if not isinstance(settings.get('bindings',{}),dict):settings['bindings']={}
        f=ttk.Frame(self,padding=18);f.pack()
        self.enabled=tk.BooleanVar(value=settings.get('enabled',False))
        ttk.Checkbutton(f,text='Enable wind inputs on this computer',variable=self.enabled).grid(row=0,columnspan=2,sticky='w',pady=8)
        self.keys={}
        for row,(key,default) in enumerate([('left','P'),('right','O'),('forward','K'),('backward','L')],1):
            self.keys[key]=tk.StringVar(value=settings.get('bindings',{}).get(key,default))
            ttk.Label(f,text='Thrust '+key.upper()).grid(row=row,column=0,sticky='w')
            ttk.Entry(f,textvariable=self.keys[key],width=12).grid(row=row,column=1,pady=4)
        self.method=tk.StringVar(value=settings.get('method','scan'))
        ttk.Label(f,text='Input method').grid(row=5,column=0,sticky='w');ttk.Combobox(f,textvariable=self.method,values=['scan','legacy'],state='readonly',width=12).grid(row=5,column=1)
        ttk.Label(f,text='Add these four plain-letter secondary bindings in Elite first.\nUse thrust forward/backward, not throttle increase/decrease.\nKeep your HOTAS bindings. Remove conflicts with other actions.\n\nRestart Story Mode after saving. Startup has a 10-second arming delay.\nNo emergency-stop hotkeys. Close Story Mode to stop wind.\nFocus loss/modifiers pause it; resume after 2 clear seconds.\nOn foot uses WASD when enabled in the zone. Shift bypasses wind.\nNo SRV wind. Editor simulations never send keys.',wraplength=520).grid(row=6,columnspan=2,pady=12,sticky='w')
        ttk.Button(f,text='Save',command=self.save).grid(row=7,column=1,sticky='e')
    def save(self):
        try:
            b={k:v.get().strip().upper() for k,v in self.keys.items()};validate_bindings(b)
            if self.enabled.get() and not messagebox.askokcancel('Confirm Elite bindings','Have you set these four thrust bindings in Elite and checked for conflicting actions?',parent=self):return
            atomic_write_json(self.path,{'enabled':self.enabled.get(),'bindings':b,'method':self.method.get()})
            self.destroy()
        except (ValueError,OSError) as e:messagebox.showerror('Wind settings',str(e),parent=self)

class WindPauseDialog(tk.Toplevel):
    def __init__(self,parent,options,initial=None):
        super().__init__(parent);self.title('Modify wind — Pause wind');self.result=None
        self.transient(parent);self.grab_set()
        initial=initial or {};self.target=tk.StringVar(value=initial.get('target_layer',options[0] if options else ''))
        self.options=options
        frame=ttk.Frame(self,padding=18);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Action: Pause wind while this environment is active').pack(anchor='w',pady=6)
        ttk.Label(frame,text='Wind layer').pack(anchor='w')
        ttk.Combobox(frame,textvariable=self.target,values=options,state='readonly',width=72).pack(fill='x',pady=6)
        ttk.Label(frame,text='Wind resumes automatically when this environment ends. Overlapping\nshelters keep it paused until all have ended. The weather clock continues.\nUses this environment’s location, conditions and vehicle selection.\nPauses movement inputs only; forecast audio and countdown keep running.',wraplength=650).pack(anchor='w',pady=12)
        ttk.Button(frame,text='Use wind pause',command=self.accept).pack(side='right')
        ttk.Button(frame,text='Cancel',command=self.destroy).pack(side='right',padx=8)
    def accept(self):
        if self.target.get() not in self.options:
            messagebox.showerror('Wind layer','Choose an existing wind layer.',parent=self);return
        self.result={'action':'pause_wind','target_layer':self.target.get()};self.destroy()

def main():
    root=tk.Tk();root.withdraw();dialog=WindSettingsDialog(root);root.wait_window(dialog);root.destroy()

if __name__=='__main__':main()
