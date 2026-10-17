"""Background jobs. Importing this package registers every job with the registry.

To add a job: decorate it with `@job(...)` (see app/jobs/registry.py) in your own module,
then add an import of that module below.
"""

from app.jobs import registry  # noqa: F401  (built-in jobs)
from app.jobs.registry import JOBS, JobAlreadyRunning, UnknownJob, job, run_job  # noqa: F401

import app.jobs.risk_jobs  # noqa: F401,E402  -> nightly_risk_assessment
import app.services.transcription_jobs  # noqa: F401,E402  -> process_pending_uploads
import app.services.booking_agent  # noqa: F401,E402  -> appointment_followups

# Other owners: import your job modules here, e.g.
# import app.services.risk_jobs  # noqa: F401  -> nightly_risk_assessment
# import app.services.ingest_jobs  # noqa: F401  -> process_pending_uploads
# import app.services.booking_jobs  # noqa: F401  -> appointment_followups
