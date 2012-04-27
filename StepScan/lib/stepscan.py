#!/usr/bin/env python
"""
Classes and Functions for simple step scanning for epics.

This does not used the Epics SScan Record, and the scan is intended to run
as a python application, but many concepts from the Epics SScan Record are
borrowed.  Where appropriate, the difference will be noted here.

A Step Scan consists of the following objects:
   a list of Positioners
   a list of Triggers
   a list of Counters

Each Positioner will have a list (or numpy array) of position values
corresponding to the steps in the scan.  As there is a fixed number of
steps in the scan, the position list for each positioners must have the
same length -- the number of points in the scan.  Note that, unlike the
SScan Record, the list of points (not start, stop, step, npts) must be
given.  Also note that the number of positioners or number of points is not
limited.

A Trigger is simply an Epics PV that will start a particular detector,
usually by having 1 written to its field.  It is assumed that when the
Epics ca.put() to the trigger completes, the Counters associated with the
triggered detector will be ready to read.

A Counter is simple a PV whose value should be recorded at every step in
the scan.  Any PV can be a Counter, including waveform records.  For many
detector types, it is possible to build a specialized class that creates
many counters.

Because Triggers and Counters are closely associated with detectors, a
Detector is also defined, which simply contains a single Trigger and a list
of Counters, and will cover most real use cases.

In addition to the core components (Positioners, Triggers, Counters, Detectors),
a Step Scan contains the following objects:

   breakpoints   a list of scan indices at which to pause and write data
                 collected so far to disk.
   extra_pvs     a list of (description, PV) tuples that are recorded at
                 the beginning of scan, and at each breakpoint, to be
                 recorded to disk file as metadata.
   pre_scan()    method to run prior to scan.
   post_scan()   method to run after scan.
   at_break()    method to run at each breakpoint.

Note that Postioners and Detectors may add their own pieces into extra_pvs,
pre_scan(), post_scan(), and at_break().

With these concepts, a Step Scan ends up being a fairly simple loop, going
roughly (that is, skipping error checking) as:

   pos = <DEFINE POSITIONER LIST>
   det = <DEFINE DETECTOR LIST>
   run_pre_scan(pos, det)
   [p.move_to_start() for p in pos]
   record_extra_pvs(pos, det)
   for i in range(len(pos[0].array)):
       [p.move_to_pos(i) for p in pos]
       while not all([p.done for p in pos]):
           time.sleep(0.001)
       [trig.start() for trig in det.triggers]
       while not all([trig.done for trig in det.triggers]):
           time.sleep(0.001)
       [det.read() for det in det.counters]

       if i in breakpoints:
           write_data(pos, det)
           record_exrta_pvs(pos, det)
           run_at_break(pos, det)
   write_data(pos, det)
   run_post_scan(pos, det)

Note that multi-dimensional mesh scans over a rectangular grid is not
explicitly supported, but these can be easily emulated with the more
flexible mechanism of unlimited list of positions and breakpoints.
Non-mesh scans are also possible.

A step scan can have an Epics SScan Record or StepScan database associated
with it.  It will use these for PVs to post data at each point of the scan.
"""

import time
import threading
from epics import PV, poll
from ordereddict import OrderedDict
from .detectors import Counter, DeviceCounter
from .outputfile import ASCIIScanFile

class ScanMessenger(threading.Thread):
    """ Provides a way to run user-supplied functions per scan point,
    in a separate thread so as to not delay scan operation.

    Initialize a ScanMessenger with a function to call per point, and the
    StepScan instance.   On .start(), a separate thread will createrd and
    the .run() method run.  Here, this runs a loop, looking at the .cpt
    attribute.  When this .cpt changes, the executing will run the user
    supplied code with arguments of 'scan=scan instance', and 'cpt=cpt'

    Thus, at each point in the scan the scanning process should set .cpt,
    and the user-supplied func will execute.

    To stop the thread, set .cpt to None.  The thread will also automatically
    stop if .cpt has not changed in more than 1 hour
    """
    # number of seconds to wait for .cpt to change before exiting thread
    timeout = 3600.
    def __init__(self, func=None, scan=None,
                 cpt=-1, npts=None, func_kws=None):
        threading.Thread.__init__(self)
        self.func = func
        self.scan = scan
        self.cpt = cpt
        self.npts = npts
        if func_kws is None:
            func_kws = {}
        self.func_kws = func_kws
        self.func_kws['npts'] = npts

    def run(self):
        """execute thread, watching the .cpt attribute. Any chnage will
        cause self.func(cpt=self.cpt, scan=self.scan) to be run.
        The thread will stop when .pt == None or has not changed in
        a time  > .timeout
        """
        last_point = self.cpt
        t0 = time.time()
        while True:
            time.sleep(0.001)
            if self.cpt != last_point:
                last_point =  self.cpt
                t0 = time.time()
                if hasattr(self.func, '__call__'):
                    self.func(cpt=self.cpt, scan=self.scan,
                              **self.func_kws)
            if self.cpt is None or time.time()-t0 > self.timeout:
                return

class StepScan(object):
    """General Step Scanning for Epics
    """
    def __init__(self, datafile=None, messenger=None):
        self.pos_settle_time = 1.e-3
        self.pos_maxmove_time = 3600.0
        self.det_settle_time = 1.e-3
        self.det_maxcount_time = 86400.0
        self.extra_pvs = []
        self.positioners = []
        self.triggers = []
        self.counters = []
        self._det = []
        self.breakpoints = []
        self.at_break_methods = []
        self.pre_scan_methods = []
        self.post_scan_methods = []
        self.pos_actual  = []

        self.datafile = datafile
        self.verified = False
        self.abort = False

        self.message_thread = None
        self.messenger = messenger
        if datafile is not None:
            self.datafile = ASCIIFile(filename=datafile)

    def add_counter(self, counter, label=None):
        "add simple counter"
        if isinstance(counter, str):
            counter = Counter(counter, label)
        if isinstance(counter, (Counter, DeviceCounter)):
            self.counters.append(counter)
        else:
            print 'Cannot add Counter? ', counter
        self.verified = False

    def add_extra_pvs(self, extra_pvs):
        """add extra pvs (tuple of (desc, pvname))"""
        if len(extra_pvs) == 0:
            return
        for desc, pvname in extra_pvs:
            if isinstance(pvname, str):
                pv = PV(pvname)
            else:
                pv = pvname
            self.extra_pvs.append((desc, pv))

    def add_positioner(self, pos):
        """ add a Positioner """
        self.add_extra_pvs(pos.extra_pvs)
        self.at_break_methods.append(pos.at_break)
        self.post_scan_methods.append(pos.post_scan)
        self.pre_scan_methods.append(pos.pre_scan)
        self.positioners.append(pos)
        self.verified = False

    def add_detector(self, det):
        """ add a Detector -- needs to be derived from Detector_Mixin"""
        self.add_extra_pvs(det.extra_pvs)
        self.at_break_methods.append(det.at_break)
        self.post_scan_methods.append(det.post_scan)
        self.pre_scan_methods.append(det.pre_scan)

        self.triggers.append(det.trigger)
        self.counters.extend(det.counters)
        self._det.append(det)
        self.verified = False

    def set_dwelltime(self, dtime):
        """set scan dwelltime per point to constant value"""
        for d in self._det:
            d.dwelltime = dtime

    def at_break(self, breakpoint=0):
        out = [m(breakpoint=breakpoint) for m in self.at_break_methods]
        if self.datafile is not None:
            self.datafile.write_data(breakpoint=breakpoint)
        return out

    def pre_scan(self):
        [pv.connect() for  (desc, pv) in self.extra_pvs]
        return [m() for m in self.pre_scan_methods]

    def post_scan(self):
        return [m() for m in self.post_scan_methods]

    def verify_scan(self):
        """ this does some simple checks of Scans, checking that
        the length of the positions array matches the length of the
        positioners array.

        For each Positioner, the max and min position is checked against
        the HLM and LLM field (if available)
        """
        npts = None
        self.error_message = ''
        for p in self.positioners:
            if not p.verify_array():
                self.error_message = 'Positioner %s array out of bounds' % p.pv.pvname
                return False
            if npts is None:
                npts = len(p.array)
            if len(p.array) != npts:
                self.error_message = 'Inconsistent positioner array length'
                return False
        return True

    def check_outputs(self, out, msg='unknown'):
        """ check outputs of a previous command
            Any True value indicates an error
        That is, return values must be None or evaluate to False
        to indicate success.
        """
        if any(out):
            raise Warning('error on output: %s' % msg)

    def read_extra_pvs(self):
        "read values for extra PVs"
        return [(desc, pv.pvname, pv.get()) for desc, pv in self.extra_pvs]


    def run(self, filename='test.dat'):
        """ run the actual scan:
           Verify, Save original positions,
           Setup output files and messenger thread,
           run pre_scan methods
           Loop over points
           run post_scan methods
        """
        if not self.verify_scan():
            print 'Cannot execute scan'
            print self.error_message
            return
        self.abort = False

        out = self.pre_scan()
        self.check_outputs(out, msg='pre scan')

        orig_positions = [p.current() for p in self.positioners]

        out = [p.move_to_start() for p in self.positioners]
        self.check_outputs(out, msg='move to start')

        self.datafile = ASCIIScanFile(filename=filename, scan=self)
        self.datafile.write_data(breakpoint=0)

        npts = len(self.positioners[0].array)

        self.message_thread = None
        if hasattr(self.messenger, '__call__'):
            self.message_thread = ScanMessenger(func=self.messenger,
                                                scan = self, npts=npts, cpt=0)
            self.message_thread.start()
        self.cpt = 0
        self.npts = npts
        for i in range(npts):
            try:
                self.cpt = i+1
                if self.abort:
                    break
                # move to next position, wait for moves to finish
                [p.move_to_pos(i) for p in self.positioners]
                t0 = time.time()
                while (not all([p.done for p in self.positioners]) and
                       time.time() - t0 < self.pos_maxmove_time):
                    if self.abort:
                        break
                    poll(1.e-3, 0.1)

                # wait for positioners to settle
                time.sleep(self.pos_settle_time)

                # start triggers, wait for them to finish
                [trig.start(1) for trig in self.triggers]
                t0 = time.time()
                while (not all([trig.done for trig in self.triggers]) and
                       time.time() - t0 < self.det_maxcount_time):
                    if self.abort:
                        break
                    poll(1.e-3, 0.1)

                # wait, then read read counters and acutal positions
                time.sleep(self.det_settle_time)
                [c.read() for c in self.counters]
                self.pos_actual.append([p.current() for p in self.positioners])

                # if a messenger exists, or this is a breakpoint, execute those
                if self.message_thread is not None:
                    self.message_thread.cpt = self.cpt

                if i in self.breakpoints:
                    self.at_break(breakpoint=i, clear=True)
            except KeyboardInterrupt:
                self.abort = True

        # scan complete
        # if completed without abort, return to original positions, write data
        if not self.abort:
            for val, pos in zip(orig_positions, self.positioners):
                pos.move_to(val, wait=False)
            self.datafile.write_data(breakpoint=-1, close_file=True)
        self.abort = False

        # run post_scan methods
        out = self.post_scan()
        self.check_outputs(out, msg='pre scan')

        # end messenger thread
        if self.message_thread is not None:
            self.message_thread.cpt = None
            self.message_thread.join()

