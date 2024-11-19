__package__ = 'archivebox.crawls'

from typing import ClassVar
from datetime import timedelta
from django.utils import timezone

from statemachine import State, StateMachine

from workers.actor import ActorType
from crawls.models import Crawl


class CrawlMachine(StateMachine, strict_states=True):
    """State machine for managing Crawl lifecycle."""
    
    model: Crawl
    
    # States
    queued = State(value=Crawl.StatusChoices.QUEUED, initial=True)
    started = State(value=Crawl.StatusChoices.STARTED)
    sealed = State(value=Crawl.StatusChoices.SEALED, final=True)
    
    # Tick Event
    tick = (
        queued.to.itself(unless='can_start') |
        queued.to(started, cond='can_start') |
        started.to.itself(unless='is_finished') |
        started.to(sealed, cond='is_finished')
    )
    
    def __init__(self, crawl, *args, **kwargs):
        self.crawl = crawl
        super().__init__(crawl, *args, **kwargs)
        
    def can_start(self) -> bool:
        return bool(self.crawl.seed and self.crawl.seed.uri)
        
    def is_finished(self) -> bool:
        from core.models import Snapshot, ArchiveResult
        
        # check that at least one snapshot exists for this crawl
        snapshots = Snapshot.objects.filter(crawl=self.crawl)
        if not snapshots.exists():
            return False
        
        # check to make sure no snapshots are in non-final states
        if snapshots.filter(status__in=[Snapshot.StatusChoices.QUEUED, Snapshot.StatusChoices.STARTED]).exists():
            return False
        
        # check that some archiveresults exist for this crawl
        results = ArchiveResult.objects.filter(snapshot__crawl=self.crawl)
        if not results.exists():
            return False
        
        # check if all archiveresults are finished
        if results.filter(status__in=[Crawl.StatusChoices.QUEUED, Crawl.StatusChoices.STARTED]).exists():
            return False
        
        return True
        
    # def before_transition(self, event, state):
    #     print(f"Before '{event}', on the '{state.id}' state.")
    #     return "before_transition_return"

    @started.enter
    def enter_started(self):
        print(f'CrawlMachine[{self.crawl.ABID}].on_started(): crawl.create_root_snapshot() + crawl.bump_retry_at(+10s)')
        # lock the crawl object for 2s while we create the root snapshot
        self.crawl.update_for_workers(
            retry_at=timezone.now() + timedelta(seconds=5),
            status=Crawl.StatusChoices.QUEUED,
        )
        assert self.crawl.create_root_snapshot()
        
        # only update status to STARTED once root snapshot is created
        self.crawl.update_for_workers(
            retry_at=timezone.now() + timedelta(seconds=5),
            status=Crawl.StatusChoices.STARTED,
        )

    @sealed.enter        
    def enter_sealed(self):
        print(f'CrawlMachine[{self.crawl.ABID}].on_sealed(): crawl.retry_at=None')
        self.crawl.update_for_workers(
            retry_at=None,
            status=Crawl.StatusChoices.SEALED,
        )


class CrawlWorker(ActorType[Crawl]):
    """The Actor that manages the lifecycle of all Crawl objects"""
    
    Model = Crawl
    StateMachineClass = CrawlMachine
    
    ACTIVE_STATE: ClassVar[State] = CrawlMachine.started
    FINAL_STATES: ClassVar[list[State]] = CrawlMachine.final_states
    STATE_FIELD_NAME: ClassVar[str] = Crawl.state_field_name
    
    MAX_CONCURRENT_ACTORS: ClassVar[int] = 3
    MAX_TICK_TIME: ClassVar[int] = 10
    CLAIM_FROM_TOP_N: ClassVar[int] = MAX_CONCURRENT_ACTORS * 10

