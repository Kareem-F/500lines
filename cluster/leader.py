from util import defaultlist, view_primary
from protocol import Ballot, ALPHA, CommanderId
from member import Component
from scout import Scout
from commander import Commander

class Leader(Component):

    def __init__(self, member, unique_id, peer_history, commander_cls=Commander, scout_cls=Scout):
        super(Leader, self).__init__(member)
        self.ballot_num = Ballot(-1, 0, unique_id)
        self.active = False
        self.proposals = defaultlist()
        self.commander_cls = commander_cls
        self.commanders = {}
        self.scout_cls = scout_cls
        self.scout = None
        self.viewid = -1
        self.peers = None
        self.peer_history = peer_history

    def on_update_peer_history_event(self, peer_history):
        self.peer_history = peer_history

    def on_view_change_event(self, slot, viewid, peers):
        self.viewid = viewid
        self.peers = peers
        self.is_primary = view_primary(viewid, peers) == self.address

        # we are not an active leader in this new view
        if self.scout:
            self.scout.finished(None, None)  # eventually calls preempted
        elif self.active:
            self.preempted(None)
        elif self.is_primary:
            self.spawn_scout()

    def spawn_scout(self):
        assert not self.scout
        self.ballot_num = Ballot(self.viewid, self.ballot_num.n, self.ballot_num.leader)
        sct = self.scout = self.scout_cls(self.member, self, self.ballot_num, self.peers)
        sct.start()

    def scout_finished(self, adopted, ballot_num, pvals):
        self.scout = None
        if adopted:
            # pvals is a defaultlist of (slot, proposal) by ballot num; we need the
            # highest ballot number for each slot.  TODO: this is super
            # inefficient!
            last_by_slot = defaultlist()
            for b, s in reversed(sorted(pvals.keys())):
                p = pvals[b, s]
                if last_by_slot[s] is None:
                    last_by_slot[s] = p
            for s, p in enumerate(last_by_slot):
                if p is not None:
                    self.proposals[s] = p
            # re-spawn commanders for any potentially outstanding proposals
            for view_slot in sorted(self.peer_history):
                slot = view_slot + ALPHA
                if self.proposals[slot] is not None:
                    self.spawn_commander(self.ballot_num, slot, self.proposals[slot],
                                         self.peer_history[view_slot])
            # note that we don't re-spawn commanders here; if there are undecided
            # proposals, the replicas will re-propose
            self.logger.info("leader becoming active")
            self.active = True
        else:
            self.preempted(ballot_num)

    def preempted(self, ballot_num):
        # ballot_num is None when we are preempted by a view change
        if ballot_num:
            self.logger.info("leader preempted by %s" % (ballot_num.leader,))
        else:
            self.logger.info("leader preempted by view change")
        self.active = False
        self.ballot_num = Ballot(
            self.viewid,
            (ballot_num if ballot_num else self.ballot_num).n + 1,
            self.ballot_num.leader)
        # if we're the primary for this view, re-scout immediately
        if not self.scout and self.is_primary:
            self.logger.info("re-scouting as the primary for this view")
            self.spawn_scout()

    def spawn_commander(self, ballot_num, slot, proposal, peers):
        peers = self.peer_history[slot - ALPHA]
        commander_id = CommanderId(self.address, slot, self.proposals[slot])
        if commander_id in self.commanders:
            return
        cmd = self.commander_cls(self.member, self, ballot_num, slot, proposal, commander_id, peers)
        self.commanders[commander_id] = cmd
        cmd.start()

    def commander_finished(self, commander_id, ballot_num, preempted):
        del self.commanders[commander_id]
        if preempted:
            self.preempted(ballot_num)

    def do_PROPOSE(self, slot, proposal):
        if self.proposals[slot] is None:
            if self.active:
                # find the peers ALPHA slots ago, or ignore if unknown
                if slot - ALPHA not in self.peer_history:
                    self.logger.warning("slot %d not in peer history %r" % (slot - ALPHA, sorted(self.peer_history)))
                    return
                self.proposals[slot] = proposal
                self.logger.warning("spawning commander for slot %d" % (slot,))
                self.spawn_commander(self.ballot_num, slot, proposal, self.peer_history[slot - ALPHA])
            else:
                if not self.scout:
                    self.logger.warning("got PROPOSE when not active - scouting")
                    self.spawn_scout()
                else:
                    self.logger.warning("got PROPOSE while scouting; ignored")
        else:
            self.logger.warning("got PROPOSE for a slot already being proposed")

