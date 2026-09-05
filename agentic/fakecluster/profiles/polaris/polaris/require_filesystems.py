# OpenPBS queuejob hook (runs as root on the server for every qsub): a job that requests no `filesystems` is ACCEPTED
# but HELD, with the reason in its comment — ALCF Polaris's shape. Release: qalter -l filesystems=home:eagle <id>;
# qrls <id>. Imported by setup.d/server-polaris.sh.
import pbs

e = pbs.event()
j = e.job
fs = j.Resource_List["filesystems"]
if fs is None or str(fs).strip() == "":
    j.Hold_Types = pbs.hold_types("u")
    j.comment = ("HELD by the site: every job must request the filesystems it uses, e.g. -l filesystems=home:eagle "
                 "(add the directive, then qrls the job or submit again)")
e.accept()
