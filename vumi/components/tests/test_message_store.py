# -*- coding: utf-8 -*-

"""Tests for vumi.components.message_store."""

from twisted.internet.defer import inlineCallbacks, returnValue

from vumi.message import TransportEvent
from vumi.application.tests.test_base import ApplicationTestCase
from vumi.components import MessageStore


class TestMessageStoreBase(ApplicationTestCase):
    # inherits from ApplicationTestCase for .mkmsg_in and .mkmsg_out

    use_riak = True

    @inlineCallbacks
    def setUp(self):
        yield super(TestMessageStoreBase, self).setUp()
        self.redis = yield self.get_redis_manager()
        self.manager = self.get_riak_manager()
        self.store = MessageStore(self.manager, self.redis)

    @inlineCallbacks
    def _maybe_batch(self, tag, by_batch):
        add_kw, batch_id = {}, None
        if tag is not None:
            batch_id = yield self.store.batch_start([tag])
            if by_batch:
                add_kw['batch_id'] = batch_id
            else:
                add_kw['tag'] = tag
        returnValue((add_kw, batch_id))

    @inlineCallbacks
    def _create_outbound(self, tag=("pool", "tag"), by_batch=False):
        """Create and store an outbound message."""
        add_kw, batch_id = yield self._maybe_batch(tag, by_batch)
        msg = self.mkmsg_out(content="outfoo",
                             message_id=TransportEvent.generate_id())
        msg_id = msg['message_id']
        yield self.store.add_outbound_message(msg, **add_kw)
        returnValue((msg_id, msg, batch_id))

    @inlineCallbacks
    def _create_inbound(self, tag=("pool", "tag"), by_batch=False):
        """Create and store an inbound message."""
        add_kw, batch_id = yield self._maybe_batch(tag, by_batch)
        msg = self.mkmsg_in(content="infoo", to_addr="+1234567810001",
                            transport_type="sms",
                            message_id=TransportEvent.generate_id())
        msg_id = msg['message_id']
        yield self.store.add_inbound_message(msg, **add_kw)
        returnValue((msg_id, msg, batch_id))

    def _batch_status(self, ack=0, nack=0, delivered=0, failed=0, pending=0,
                        sent=0):
        return {
            'ack': ack, 'nack': nack, 'sent': sent,
            'delivery_report': sum([delivered, failed, pending]),
            'delivery_report.delivered': delivered,
            'delivery_report.failed': failed,
            'delivery_report.pending': pending,
            }


class TestMessageStore(TestMessageStoreBase):

    @inlineCallbacks
    def test_batch_start(self):
        tag1 = ("poolA", "tag1")
        batch_id = yield self.store.batch_start([tag1])
        batch = yield self.store.get_batch(batch_id)
        tag_info = yield self.store.get_tag_info(tag1)
        batch_messages = yield self.store.batch_messages(batch_id)
        batch_status = yield self.store.batch_status(batch_id)
        self.assertEqual(batch_messages, [])
        self.assertEqual(list(batch.tags), [tag1])
        self.assertEqual(tag_info.current_batch.key, batch_id)
        self.assertEqual(batch_status, self._batch_status())

    @inlineCallbacks
    def test_batch_start_with_metadata(self):
        batch_id = yield self.store.batch_start([], key1=u"foo", key2=u"bar")
        batch = yield self.store.get_batch(batch_id)
        self.assertEqual(batch.metadata['key1'], "foo")
        self.assertEqual(batch.metadata['key2'], "bar")

    @inlineCallbacks
    def test_batch_done(self):
        tag1 = ("poolA", "tag1")
        batch_id = yield self.store.batch_start([tag1])
        yield self.store.batch_done(batch_id)
        batch = yield self.store.get_batch(batch_id)
        tag_info = yield self.store.get_tag_info(tag1)
        self.assertEqual(list(batch.tags), [tag1])
        self.assertEqual(tag_info.current_batch.key, None)

    @inlineCallbacks
    def test_add_outbound_message(self):
        msg_id, msg, _batch_id = yield self._create_outbound(tag=None)

        stored_msg = yield self.store.get_outbound_message(msg_id)
        self.assertEqual(stored_msg, msg)
        events = yield self.store.message_events(msg_id)
        self.assertEqual(events, [])

    @inlineCallbacks
    def test_add_outbound_message_with_batch_id(self):
        msg_id, msg, batch_id = yield self._create_outbound(by_batch=True)

        stored_msg = yield self.store.get_outbound_message(msg_id)
        batch_messages = yield self.store.batch_messages(batch_id)
        message_events = yield self.store.message_events(msg_id)
        batch_status = yield self.store.batch_status(batch_id)

        self.assertEqual(stored_msg, msg)
        self.assertEqual(batch_messages, [msg])
        self.assertEqual(message_events, [])
        self.assertEqual(batch_status, self._batch_status(sent=1))

    @inlineCallbacks
    def test_add_outbound_message_with_tag(self):
        msg_id, msg, batch_id = yield self._create_outbound()

        stored_msg = yield self.store.get_outbound_message(msg_id)
        batch_messages = yield self.store.batch_messages(batch_id)
        message_events = yield self.store.message_events(msg_id)
        batch_status = yield self.store.batch_status(batch_id)

        self.assertEqual(stored_msg, msg)
        self.assertEqual(batch_messages, [msg])
        self.assertEqual(message_events, [])
        self.assertEqual(batch_status, self._batch_status(sent=1))

    @inlineCallbacks
    def test_add_ack_event(self):
        msg_id, msg, batch_id = yield self._create_outbound()
        ack = self.mkmsg_ack(user_message_id=msg_id)
        ack_id = ack['event_id']
        yield self.store.add_event(ack)

        stored_ack = yield self.store.get_event(ack_id)
        message_events = yield self.store.message_events(msg_id)
        batch_status = yield self.store.batch_status(batch_id)

        self.assertEqual(stored_ack, ack)
        self.assertEqual(message_events, [ack])
        self.assertEqual(batch_status, self._batch_status(sent=1, ack=1))

    @inlineCallbacks
    def test_add_nack_event(self):
        msg_id, msg, batch_id = yield self._create_outbound()
        nack = self.mkmsg_nack(user_message_id=msg_id)
        nack_id = nack['event_id']
        yield self.store.add_event(nack)

        stored_nack = yield self.store.get_event(nack_id)
        message_events = yield self.store.message_events(msg_id)
        batch_status = yield self.store.batch_status(batch_id)

        self.assertEqual(stored_nack, nack)
        self.assertEqual(message_events, [nack])
        self.assertEqual(batch_status, self._batch_status(sent=1, nack=1))

    @inlineCallbacks
    def test_add_ack_event_without_batch(self):
        msg_id, msg, _batch_id = yield self._create_outbound(tag=None)
        ack = self.mkmsg_ack(user_message_id=msg_id)
        ack_id = ack['event_id']
        yield self.store.add_event(ack)

        stored_ack = yield self.store.get_event(ack_id)
        message_events = yield self.store.message_events(msg_id)

        self.assertEqual(stored_ack, ack)
        self.assertEqual(message_events, [ack])

    @inlineCallbacks
    def test_add_nack_event_without_batch(self):
        msg_id, msg, _batch_id = yield self._create_outbound(tag=None)
        nack = self.mkmsg_nack(user_message_id=msg_id)
        nack_id = nack['event_id']
        yield self.store.add_event(nack)

        stored_nack = yield self.store.get_event(nack_id)
        message_events = yield self.store.message_events(msg_id)

        self.assertEqual(stored_nack, nack)
        self.assertEqual(message_events, [nack])

    @inlineCallbacks
    def test_add_delivery_report_events(self):
        msg_id, msg, batch_id = yield self._create_outbound()

        drs = []
        for status in TransportEvent.DELIVERY_STATUSES:
            dr = self.mkmsg_delivery(user_message_id=msg_id,
                                        status=status)
            dr_id = dr['event_id']
            drs.append(dr)
            yield self.store.add_event(dr)
            stored_dr = yield self.store.get_event(dr_id)
            self.assertEqual(stored_dr, dr)

        message_events = yield self.store.message_events(msg_id)
        message_events.sort(key=lambda msg: msg['event_id'])
        drs.sort(key=lambda msg: msg['event_id'])
        self.assertEqual(message_events, drs)
        dr_counts = dict((status, 1)
                         for status in TransportEvent.DELIVERY_STATUSES)
        batch_status = yield self.store.batch_status(batch_id)
        self.assertEqual(batch_status, self._batch_status(sent=1, **dr_counts))

    @inlineCallbacks
    def test_add_inbound_message(self):
        msg_id, msg, _batch_id = yield self._create_inbound(tag=None)
        stored_msg = yield self.store.get_inbound_message(msg_id)
        self.assertEqual(stored_msg, msg)

    @inlineCallbacks
    def test_add_inbound_message_with_batch_id(self):
        msg_id, msg, batch_id = yield self._create_inbound(by_batch=True)

        stored_msg = yield self.store.get_inbound_message(msg_id)
        batch_replies = yield self.store.batch_replies(batch_id)

        self.assertEqual(stored_msg, msg)
        self.assertEqual(batch_replies, [msg])

    @inlineCallbacks
    def test_add_inbound_message_with_tag(self):
        msg_id, msg, batch_id = yield self._create_inbound()

        stored_msg = yield self.store.get_inbound_message(msg_id)
        batch_replies = yield self.store.batch_replies(batch_id)

        self.assertEqual(stored_msg, msg)
        self.assertEqual(batch_replies, [msg])

    @inlineCallbacks
    def test_inbound_counts(self):
        _msg_id, _msg, batch_id = yield self._create_inbound(by_batch=True)
        self.assertEqual(1, (yield self.store.batch_inbound_count(batch_id)))
        yield self.store.add_inbound_message(self.mkmsg_in(
                message_id=TransportEvent.generate_id()), batch_id=batch_id)
        self.assertEqual(2, (yield self.store.batch_inbound_count(batch_id)))

    @inlineCallbacks
    def test_outbound_counts(self):
        _msg_id, _msg, batch_id = yield self._create_outbound(by_batch=True)
        self.assertEqual(1, (yield self.store.batch_outbound_count(batch_id)))
        yield self.store.add_outbound_message(self.mkmsg_out(
                message_id=TransportEvent.generate_id()), batch_id=batch_id)
        self.assertEqual(2, (yield self.store.batch_outbound_count(batch_id)))


class TestMessageStoreCache(TestMessageStoreBase):

    @inlineCallbacks
    def test_cache_batch_start(self):
        batch_id = yield self.store.batch_start([("poolA", "tag1")])
        self.assertTrue((yield self.store.cache.batch_exists(batch_id)))
        self.assertTrue(batch_id in (yield self.store.cache.get_batch_ids()))

    @inlineCallbacks
    def test_cache_add_outbound_message(self):
        msg_id, msg, batch_id = yield self._create_outbound()
        [cached_msg_id] = (yield
            self.store.cache.get_outbound_message_keys(batch_id))
        [cached_to_addr] = (yield
            self.store.cache.get_to_addrs(batch_id))
        self.assertEqual(msg_id, cached_msg_id)
        self.assertEqual(msg['to_addr'], cached_to_addr)

    @inlineCallbacks
    def test_cache_add_inbound_message(self):
        msg_id, msg, batch_id = yield self._create_inbound()
        [cached_msg_id] = (yield
            self.store.cache.get_inbound_message_keys(batch_id))
        [cached_from_addr] = (yield
            self.store.cache.get_from_addrs(batch_id))
        self.assertEqual(msg_id, cached_msg_id)
        self.assertEqual(msg['from_addr'], cached_from_addr)

    @inlineCallbacks
    def test_cache_add_event(self):
        msg_id, msg, batch_id = yield self._create_outbound()
        ack = TransportEvent(user_message_id=msg_id, event_type='ack',
                             sent_message_id='xyz')
        yield self.store.add_event(ack)
        self.assertEqual((yield self.store.cache.get_event_status(batch_id)), {
            'delivery_report': 0,
            'delivery_report.delivered': 0,
            'delivery_report.failed': 0,
            'delivery_report.pending': 0,
            'ack': 1,
            'nack': 0,
            'sent': 1,
        })
