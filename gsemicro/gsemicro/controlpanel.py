import wx
import time
import json

from epics import Motor
from epics.wx import MotorPanel, EpicsFunction

from epics.wx.utils import (add_button, add_menu, popup, pack, Closure ,
                            NumericCombo, SimpleText, FileSave, FileOpen,
                            SelectWorkdir, LTEXT, CEN, LCEN, RCEN, RIGHT)

from .icons import bitmaps
from .station_configs import station_configs

ALL_EXP  = wx.ALL|wx.EXPAND|wx.ALIGN_LEFT|wx.ALIGN_TOP
LEFT_BOT = wx.ALIGN_LEFT|wx.ALIGN_BOTTOM
CEN_TOP  = wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_TOP
CEN_BOT  = wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_BOTTOM

def make_steps(precision=3, min_step=0, max_steps=10, decades=7, steps=(1,2,5)):
    """automatically create list of step sizes, generally going as
        1, 2, 5, 10, 20, 50, 100, 200, 500
    using precision,
    """
    steps = []
    for i in range(decades):
        for step in (j* 10**(i-precision) for j in steps):
            if (step <= max_step and step > 0.98*min_step):
                steps.append(step)
    return steps

class ControlPanel(wx.Panel):
    def __init__(self, parent):
        wx.Panel.__init__(self, parent, -1, station='Station_13IDE')

        self.config = json.loads(station_configs[station.upper()])
        self.tweak_wids  = {}   # tweak Combobox widgets per group
        self.groupmotors = {}   # motorlist per group
        self.motor_wids  = {}   # motor panel widgets, key=desc
        self.motors      = {}   # epics motor,         key=desc
        self.sign        = {}   # motor sign for ZFM,  key=desc
        self.SetMinSize((280, 500))

        sizer = wx.BoxSizer(wx.VERTICAL)
        for group, precision, max_step, motorlist in self.config:
            self.groupmotors[group] = []
            for pvname, desc, dsign in motorlist:
                self.groupmotors[group].append(desc)

            kws = {'motorlist': motorlist, 'max_step': max_step*1.02,
                   'precision': precision}
            if label.lower().startswith('fine'):
                kws['buttons'] = [('Zero Fine Motors', self.onZeroFineMotors)]
            sizer.Add((3, 3))
            sizer.Add(self.group_panel(group=group, **kws),   0, ALL_EXP)
            sizer.Add((3, 3))
            sizer.Add(wx.StaticLine(self, size=(290, 3)), 0, CEN_TOP)
        pack(self, sizer)
        self.connect_motors()

        @EpicsFunction
        def connect_motors(self):
            "connect to epics motors"
            for group, precision, tmax, motorlist in self.config:
                for pvname, desc, dsign in motorlist:
                    self.motors[desc] = Motor(name=pvname)
                    self.sign[desc] = dsign
            for mname in self.motor_wids:
                self.motor_wids[mname].SelectMotor(self.motors[mname])

    def group_panel(self, group='Fine Stages', motorlist=None,
                    precision=3, max_step=5.01, buttons=None):
        """make motor group panel """
        panel  = wx.Panel(self)

        tweaklist = make_steps(precision=precision, max_step=max_step)
        if group.lower().startwith('theta'):
            tweaklist.extend([10, 20, 30, 45, 90, 180])

        init_tweak = {'Focus': 5, 'Theta': 8}.get(group, 6)

        self.tweak_wids[group] = NumericCombo(panel, tweaklist,
                                              precision=precision,
                                              init=init_tweak)

        slabel = wx.BoxSizer(wx.HORIZONTAL)
        slabel.Add(wx.StaticText(panel, label=" %s: " % label, size=(120,-1)),
                   1,  wx.EXPAND|LEFT_BOT)
        slabel.Add(self.tweak_wids[group], 0,  ALL_EXP)

        msizer = wx.BoxSizer(wx.VERTICAL)
        msizer.Add(slabel, 0, ALL_EXP)

        for pvname, desc, dsign in motorlist:
            self.motor_wids[desc] = MotorPanel(panel, label=mlabel, psize='small')
            msizer.Add(self.motor_wids[desc], 0, ALL_EXP)

        if buttons is not None:
            for blabel, action in buttons:
                msizer.Add(add_button(panel, blabel, action=action))

        dim=len(motorlist)
        btnbox = self.make_button_panel(panel, group=label, dim=dim)

        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(msizer, 0, ALL_EXP)
        sizer.Add(btnbox, 0, CEN_TOP, 1)

        #for mname in self.motor_wids:
        #    self.motor_wids[mname].SelectMotor(self.motors[mname])
        pack(panel, sizer)
        return panel

    def arrow(self, panel, group, name):
        "bitmap button"
        b = wx.BitmapButton(panel, -1, bitmaps[name], style=wx.NO_BORDER)
        b.Bind(wx.EVT_BUTTON, Closure(self.onMove, group=group, name=name))
        return b

    def make_button_panel(self, parent, group='', dim=2):
        panel = wx.Panel(parent)
        if dim=2:
            sizer = wx.GridSizer(3, 3, 1, 1)
            sizer.Add(self.arrow(panel, group, 'nw'), 0, ALL_EXP)
            sizer.Add(self.arrow(panel, group, 'nn'), 0, ALL_EXP)
            sizer.Add(self.arrow(panel, group, 'ne'), 0, ALL_EXP)
            sizer.Add(self.arrow(panel, group, 'ww'), 0, ALL_EXP)
            sizer.Add((2, 2))
            sizer.Add(self.arrow(panel, group, 'ee'), 0, ALL_EXP)
            sizer.Add(self.arrow(panel, group, 'sw'), 0, ALL_EXP)
            sizer.Add(self.arrow(panel, group, 'ss'), 0, ALL_EXP)
            sizer.Add(self.arrow(panel, group, 'se'), 0, ALL_EXP)
        else:
            sizer = wx.GridSizer(1, 3)
            sizer.Add(self.arrow(panel, group, 'ww'), 0, ALL_EXP)
            sizer.Add((2, 2))
            sizer.Add(self.arrow(panel, group, 'ee'), 0, ALL_EXP)

        pack(panel, sizer)
        return panel

    def onZeroFineMotors(self, event=None):
        "event handler for Zero Fine Motors"
        mot = self.motors
        mot['x'].VAL +=  self.sign['finex'] * mot['finex'].VAL
        mot['y'].VAL +=  self.sign['finey'] * mot['finey'].VAL
        time.sleep(0.05)
        mot['finex'].VAL = 0
        mot['finey'].VAL = 0

    def onMove(self, event, name=None, group=None):
        twkval = float(self.tweak_wids[group].GetStringSelection())
        ysign = {'n':1, 's':-1}.get(name[0], 0)
        xsign = {'e':1, 'w':-1}.get(name[1], 0)

        y = None
        mdesc = self.groupmotors[group]
        x = mdesc[0]
        if len(mots) == 2:
            y = mdesc[1]

        delta = twkval * xsign * self.sign[x]
        val   = delta + float(self.motor_wids[x].drive.GetValue())
        self.motor_wids[x].drive.SetValue("%f" % val)
        if y is not None:
            delta = twkval * ysign * self.sign[y]
            val   = delta + float(self.motor_wids[y].drive.GetValue())
            self.motor_wids[y].drive.SetValue("%f" % val)
        try:
            self.motors[x].TWV = twkval
            if y is not None:
                self.motors[y].TWV = twkval
        except:
            pass

    def current_position(self):
        pos = {}
        for desc, motor in self.motors.items():
            pos[desc] = motor.VAL
        return pos
