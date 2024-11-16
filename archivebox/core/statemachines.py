__package__ = 'archivebox.snapshots'

from django.utils import timezone

from statemachine import State, StateMachine

from core.models import Snapshot, ArchiveResult

# State Machine Definitions
#################################################


class SnapshotMachine(StateMachine, strict_states=True):
    """
    State machine for managing Snapshot lifecycle.
    
    https://github.com/ArchiveBox/ArchiveBox/wiki/ArchiveBox-Architecture-Diagrams
    """
    
    model: Snapshot
    
    # States
    queued = State(value=Snapshot.StatusChoices.QUEUED, initial=True)
    started = State(value=Snapshot.StatusChoices.STARTED)
    sealed = State(value=Snapshot.StatusChoices.SEALED, final=True)
    
    # Tick Event
    tick = (
        queued.to.itself(unless='can_start') |
        queued.to(started, cond='can_start') |
        started.to.itself(unless='is_finished') |
        started.to(sealed, cond='is_finished')
    )
    
    def __init__(self, snapshot, *args, **kwargs):
        self.snapshot = snapshot
        super().__init__(snapshot, *args, **kwargs)
        
    def can_start(self) -> bool:
        return self.snapshot.url
        
    def is_finished(self) -> bool:
        if not self.snapshot.archiveresult_set.exists():
            return False
        if self.snapshot.pending_archiveresults().exists():
            return False
        return True
        
    @started.enter
    def on_started(self):
        print(f'SnapshotMachine[{self.snapshot.ABID}].on_started(): snapshot.create_pending_archiveresults() + snapshot.bump_retry_at(+60s)')
        self.snapshot.create_pending_archiveresults()
        self.snapshot.bump_retry_at(seconds=60)
        self.snapshot.save()
        
    @sealed.enter
    def on_sealed(self):
        print(f'SnapshotMachine[{self.snapshot.ABID}].on_sealed(): snapshot.retry_at=None')
        self.snapshot.retry_at = None
        self.snapshot.save()


class ArchiveResultMachine(StateMachine, strict_states=True):
    """
    State machine for managing ArchiveResult lifecycle.
    
    https://github.com/ArchiveBox/ArchiveBox/wiki/ArchiveBox-Architecture-Diagrams
    """
    
    model: ArchiveResult
    
    # States
    queued = State(value=ArchiveResult.StatusChoices.QUEUED, initial=True)
    started = State(value=ArchiveResult.StatusChoices.STARTED)
    backoff = State(value=ArchiveResult.StatusChoices.BACKOFF)
    succeeded = State(value=ArchiveResult.StatusChoices.SUCCEEDED, final=True)
    failed = State(value=ArchiveResult.StatusChoices.FAILED, final=True)
    
    # Tick Event
    tick = (
        queued.to.itself(unless='can_start') |
        queued.to(started, cond='can_start') |
        started.to.itself(unless='is_finished') |
        started.to(succeeded, cond='is_succeeded') |
        started.to(failed, cond='is_failed') |
        started.to(backoff, cond='is_backoff') |
        backoff.to.itself(unless='can_start') |
        backoff.to(started, cond='can_start') |
        backoff.to(succeeded, cond='is_succeeded') |
        backoff.to(failed, cond='is_failed')
    )

    def __init__(self, archiveresult, *args, **kwargs):
        self.archiveresult = archiveresult
        super().__init__(archiveresult, *args, **kwargs)
        
    def can_start(self) -> bool:
        return self.archiveresult.snapshot and self.archiveresult.snapshot.STATE == Snapshot.active_state
    
    def is_succeeded(self) -> bool:
        return self.archiveresult.output_exists()
    
    def is_failed(self) -> bool:
        return not self.archiveresult.output_exists()
    
    def is_backoff(self) -> bool:
        return self.archiveresult.STATE == ArchiveResult.StatusChoices.BACKOFF
    
    def is_finished(self) -> bool:
        return self.is_failed() or self.is_succeeded()

    @started.enter
    def on_started(self):
        print(f'ArchiveResultMachine[{self.archiveresult.ABID}].on_started(): archiveresult.start_ts + create_output_dir() + bump_retry_at(+60s)')
        self.archiveresult.start_ts = timezone.now()
        self.archiveresult.create_output_dir()
        self.archiveresult.bump_retry_at(seconds=60)
        self.archiveresult.save()

    @backoff.enter
    def on_backoff(self):
        print(f'ArchiveResultMachine[{self.archiveresult.ABID}].on_backoff(): archiveresult.bump_retry_at(+60s)')
        self.archiveresult.bump_retry_at(seconds=60)
        self.archiveresult.save()

    @succeeded.enter
    def on_succeeded(self):
        print(f'ArchiveResultMachine[{self.archiveresult.ABID}].on_succeeded(): archiveresult.end_ts')
        self.archiveresult.end_ts = timezone.now()
        self.archiveresult.save()

    @failed.enter
    def on_failed(self):
        print(f'ArchiveResultMachine[{self.archiveresult.ABID}].on_failed(): archiveresult.end_ts')
        self.archiveresult.end_ts = timezone.now()
        self.archiveresult.save()
        
    # def after_transition(self, event: str, source: State, target: State):
    #     print(f"after '{event}' from '{source.id}' to '{target.id}'")
    #     # self.archiveresult.save_merkle_index()
    #     # self.archiveresult.save_html_index()
    #     # self.archiveresult.save_json_index()
    #     return "after_transition"
