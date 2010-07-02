# Copyright (C) 2007, Red Hat, Inc.
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.

"""UI interface to an activity in the presence service

STABLE.
"""

import logging
from functools import partial

import dbus
import gobject
import telepathy
from telepathy.client import Channel
from telepathy.interfaces import CHANNEL, \
                                 CHANNEL_INTERFACE_GROUP, \
                                 CHANNEL_TYPE_TUBES, \
                                 CHANNEL_TYPE_TEXT, \
                                 CONNECTION, \
                                 PROPERTIES_INTERFACE
from telepathy.constants import CHANNEL_GROUP_FLAG_CHANNEL_SPECIFIC_HANDLES, \
                                HANDLE_TYPE_ROOM, \
                                PROPERTY_FLAG_WRITE

from sugar.presence.buddy import Buddy

CONN_INTERFACE_ACTIVITY_PROPERTIES = 'org.laptop.Telepathy.ActivityProperties'
CONN_INTERFACE_BUDDY_INFO = 'org.laptop.Telepathy.BuddyInfo'

_logger = logging.getLogger('sugar.presence.activity')


class Activity(gobject.GObject):
    """UI interface for an Activity in the presence service

    Activities in the presence service represent your and other user's
    shared activities.

    Properties:
        id
        color
        name
        type
        joined
    """
    __gsignals__ = {
        'buddy-joined': (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
            ([gobject.TYPE_PYOBJECT])),
        'buddy-left': (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
            ([gobject.TYPE_PYOBJECT])),
        'new-channel': (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
            ([gobject.TYPE_PYOBJECT])),
        'joined': (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
            ([gobject.TYPE_PYOBJECT, gobject.TYPE_PYOBJECT])),
    }

    __gproperties__ = {
        'id': (str, None, None, None, gobject.PARAM_READABLE),
        'name': (str, None, None, None, gobject.PARAM_READWRITE),
        'tags': (str, None, None, None, gobject.PARAM_READWRITE),
        'color': (str, None, None, None, gobject.PARAM_READWRITE),
        'type': (str, None, None, None, gobject.PARAM_READABLE),
        'private': (bool, None, None, True, gobject.PARAM_READWRITE),
        'joined': (bool, None, None, False, gobject.PARAM_READABLE),
    }

    def __init__(self, connection, room_handle=None, properties=None):
        if room_handle is None and properties is None:
            raise ValueError('Need to pass one of room_handle or properties')

        if properties is None:
            properties = {}

        gobject.GObject.__init__(self)

        self.telepathy_conn = connection
        self.telepathy_text_chan = None
        self.telepathy_tubes_chan = None

        self._room_handle = room_handle
        self._join_command = None
        self._id = properties.get('id', None)
        self._color = properties.get('color', None)
        self._name = properties.get('name', None)
        self._type = properties.get('type', None)
        self._tags = properties.get('tags', None)
        self._private = properties.get('private', True)
        self._joined = properties.get('joined', False)
        self._self_handle = None

        # The buddies really in the channel, which we can see directly because
        # we've joined. If _joined is False, this will be incomplete.
        # { member handle, possibly channel-specific => Buddy }
        self._handle_to_buddy = {}

        self._get_properties_call = None
        if not self._room_handle is None:
            self._start_tracking_properties()

    def _start_tracking_properties(self):
        bus = dbus.SessionBus()
        self._get_properties_call = bus.call_async(
                self.telepathy_conn.requested_bus_name,
                self.telepathy_conn.object_path,
                CONN_INTERFACE_ACTIVITY_PROPERTIES,
                'GetProperties',
                'u',
                (self._room_handle,),
                reply_handler=self._got_properties_cb,
                error_handler=self._error_handler_cb,
                utf8_strings=True)

        # As only one Activity instance is needed per activity process,
        # we can afford listening to ActivityPropertiesChanged like this.
        self.telepathy_conn.connect_to_signal(
                'ActivityPropertiesChanged',
                self.__activity_properties_changed_cb,
                dbus_interface=CONN_INTERFACE_ACTIVITY_PROPERTIES)

    def __activity_properties_changed_cb(self, room_handle, properties):
        _logger.debug('%r: Activity properties changed to %r', self, properties)
        self._update_properties(properties)

    def _got_properties_cb(self, properties):
        _logger.debug('_got_properties_cb %r', properties)
        self._get_properties_call = None
        self._update_properties(properties)

    def _error_handler_cb(self, error):
        _logger.debug('_error_handler_cb %r', error)

    def _update_properties(self, new_props):
        val = new_props.get('name', self._name)
        if isinstance(val, str) and val != self._name:
            self._name = val
            self.notify('name')
        val = new_props.get('tags', self._tags)
        if isinstance(val, str) and val != self._tags:
            self._tags = val
            self.notify('tags')
        val = new_props.get('color', self._color)
        if isinstance(val, str) and val != self._color:
            self._color = val
            self.notify('color')
        val = bool(new_props.get('private', self._private))
        if val != self._private:
            self._private = val
            self.notify('private')
        val = new_props.get('id', self._id)
        if isinstance(val, str) and self._id is None:
            self._id = val
            self.notify('id')
        val = new_props.get('type', self._type)
        if isinstance(val, str) and self._type is None:
            self._type = val
            self.notify('type')

    def object_path(self):
        """Get our dbus object path"""
        return self._object_path

    def do_get_property(self, pspec):
        """Retrieve a particular property from our property dictionary"""

        if pspec.name == "joined":
            return self._joined

        if self._get_properties_call is not None:
            _logger.debug('%r: Blocking on GetProperties() because someone '
                          'wants property %s', self, pspec.name)
            self._get_properties_call.block()

        if pspec.name == "id":
            return self._id
        elif pspec.name == "name":
            return self._name
        elif pspec.name == "color":
            return self._color
        elif pspec.name == "type":
            return self._type
        elif pspec.name == "tags":
            return self._tags
        elif pspec.name == "private":
            return self._private

    def do_set_property(self, pspec, val):
        """Set a particular property in our property dictionary"""
        # FIXME: need an asynchronous API to set these properties,
        # particularly 'private'

        if pspec.name == "name":
            self._name = val
        elif pspec.name == "color":
            self._color = val
        elif pspec.name == "tags":
            self._tags = val
        elif pspec.name == "private":
            self._private = val
        else:
            raise ValueError('Unknown property "%s"', pspec.name)

        self._publish_properties()

    def set_private(self, val, reply_handler, error_handler):
        _logger.debug('set_private %r', val)
        self._activity.SetProperties({'private': bool(val)},
                                     reply_handler=reply_handler,
                                     error_handler=error_handler)

    def _emit_buddy_joined_signal(self, object_path):
        """Generate buddy-joined GObject signal with presence Buddy object"""
        self.emit('buddy-joined', self._ps_new_object(object_path))
        return False

    def _buddy_handle_joined_cb(self, object_path, handle):
        _logger.debug('%r: buddy %s joined with handle %u', self, object_path,
                      handle)
        gobject.idle_add(self._emit_buddy_joined_signal, object_path)
        self._handle_to_buddy_path[handle] = object_path
        self._buddy_path_to_handle[object_path] = handle

    def _emit_buddy_left_signal(self, object_path):
        """Generate buddy-left GObject signal with presence Buddy object

        XXX note use of _ps_new_object instead of _ps_del_object here
        """
        self.emit('buddy-left', self._ps_new_object(object_path))
        return False

    def _buddy_left_cb(self, object_path):
        _logger.debug('%r: buddy %s left', self, object_path)
        gobject.idle_add(self._emit_buddy_left_signal, object_path)
        handle = self._buddy_path_to_handle.pop(object_path, None)
        if handle:
            self._handle_to_buddy_path.pop(handle, None)

    def _emit_new_channel_signal(self, object_path):
        """Generate new-channel GObject signal with channel object path

        New telepathy-python communications channel has been opened
        """
        self.emit('new-channel', object_path)
        return False

    def _new_channel_cb(self, object_path):
        _logger.debug('%r: new channel created at %s', self, object_path)
        gobject.idle_add(self._emit_new_channel_signal, object_path)

    def get_joined_buddies(self):
        """Retrieve the set of Buddy objects attached to this activity

        returns list of presence Buddy objects that we can successfully
        create from the buddy object paths that PS has for this activity.
        """
        logging.info('KILL_PS return joined buddies')
        return []

    def get_buddy_by_handle(self, handle):
        """Retrieve the Buddy object given a telepathy handle.

        buddy object paths are cached in self._handle_to_buddy_path,
        so we can get the buddy without calling PS.
        """
        object_path = self._handle_to_buddy_path.get(handle, None)
        if object_path:
            buddy = self._ps_new_object(object_path)
            return buddy
        return None

    def invite(self, buddy, message, response_cb):
        """Invite the given buddy to join this activity.

        The callback will be called with one parameter: None on success,
        or an exception on failure.
        """
        op = buddy.object_path()
        _logger.debug('%r: inviting %s', self, op)
        self._activity.Invite(op, message,
                              reply_handler=lambda: response_cb(None),
                              error_handler=response_cb)

    def set_up_tubes(self, reply_handler, error_handler):
        pass

    def __joined_cb(self, join_command, error):
        _logger.debug('%r: Join finished %r', self, error)
        if error is None:
            self._joined = True
            self.telepathy_text_chan = join_command.text_channel
            self.telepathy_tubes_chan = join_command.tubes_channel
            self._start_tracking_buddies()
            self._start_tracking_channel()
        self.emit('joined', error is None, str(error))

    def _start_tracking_buddies(self):
        group = self.telepathy_text_chan[CHANNEL_INTERFACE_GROUP]
        group.connect_to_signal('MembersChanged',
                                self.__text_channel_members_changed_cb)

    def _start_tracking_channel(self):
        channel = self.telepathy_text_chan[CHANNEL]
        channel.connect_to_signal('Closed', self.__text_channel_closed_cb)

    def __text_channel_members_changed_cb(self, message, added, removed,
                                          local_pending, remote_pending,
                                          actor, reason):
        _logger.debug('__text_channel_members_changed_cb added %r',
                      [added, message, added, removed, local_pending,
                       remote_pending, actor, reason])
        for contact_handle in added:
            self.emit('buddy-joined', Buddy(self.telepathy_conn, contact_handle))

    def join(self):
        """Join this activity.

        Emits 'joined' and otherwise does nothing if we're already joined.
        """
        if self._join_command is not None:
            return

        if self._joined:
            self.emit('joined', True, None)
            return

        _logger.debug('%r: joining', self)

        self._join_command = _JoinCommand(self.telepathy_conn,
                                          self._room_handle)
        self._join_command.connect('finished', self.__joined_cb)
        self._join_command.run()

    def share(self, share_activity_cb, share_activity_error_cb):
        if not self._room_handle is None:
            raise ValueError('Already have a room handle')

        self._share_command = _ShareCommand(self.telepathy_conn, self._id)
        self._share_command.connect('finished',
                                    partial(self.__shared_cb,
                                            share_activity_cb,
                                            share_activity_error_cb))
        self._share_command.run()

    def __shared_cb(self, share_activity_cb, share_activity_error_cb,
                    share_command, error):
        _logger.debug('%r: Share finished %r', self, error)
        if error is None:
            self._joined = True
            self._room_handle = share_command.room_handle
            self.telepathy_text_chan = share_command.text_channel
            self.telepathy_tubes_chan = share_command.tubes_channel
            self._publish_properties()
            self._start_tracking_properties()
            self._start_tracking_buddies()
            self._start_tracking_channel()
            share_activity_cb(self)
        else:
            share_activity_error_cb(self, error)

    def _publish_properties(self):
        properties = {}

        if self._color is not None:
            properties['color'] = self._color
        if self._name is not None:
            properties['name'] = self._name
        if self._type is not None:
            properties['type'] = self._type
        if self._tags is not None:
            properties['tags'] = self._tags
        properties['private'] = self._private

        logging.debug('_publish_properties calling SetProperties %r', properties)
        self.telepathy_conn.SetProperties(
                self._room_handle,
                properties,
                dbus_interface=CONN_INTERFACE_ACTIVITY_PROPERTIES)

    def __share_error_cb(self, share_activity_error_cb, error):
        logging.debug('%r: Share failed because: %s', self, error)
        share_activity_error_cb(self, error)

    # GetChannels() wrapper

    def get_channels(self):
        """Retrieve communications channel descriptions for the activity

        Returns a tuple containing:
            - the D-Bus well-known service name of the connection
              (FIXME: this is redundant; in Telepathy it can be derived
              from that of the connection)
            - the D-Bus object path of the connection
            - a list of D-Bus object paths representing the channels
              associated with this activity
        """
        (bus_name, connection, channels) = self._activity.GetChannels()
        _logger.debug('%r: bus name is %s, connection is %s, channels are %r',
                      self, bus_name, connection, channels)
        return bus_name, connection, channels

    # Leaving
    def __text_channel_closed_cb(self):
        self._joined = False
        self.emit("joined", False, "left activity")

    def leave(self):
        """Leave this shared activity"""
        _logger.debug('%r: leaving', self)
        self.telepathy_text_chan.Close()

class _BaseCommand(gobject.GObject):
    __gsignals__ = {
        'finished': (gobject.SIGNAL_RUN_FIRST, gobject.TYPE_NONE,
                     ([object])),
    }
    def __init__(self):
        gobject.GObject.__init__(self)

    def run(self):
        raise NotImplementedError()


class _ShareCommand(_BaseCommand):
    def __init__(self, connection, activity_id):
        _BaseCommand.__init__(self)

        self._connection = connection
        self._activity_id = activity_id
        self._finished = False
        self._join_command = None
        self.text_channel = None
        self.tubes_channel = None
        self.room_handle = None

    def run(self):
        """ TODO: Check we don't need this
        # We shouldn't have to do this, but Gabble sometimes finds the IRC
        # transport and goes "that has chatrooms, that'll do nicely". Work
        # around it til Gabble gets better at finding the MUC service.
        return '%s@%s' % (activity_id,
                          self._account['fallback-conference-server'])
        """

        self._connection.RequestHandles(
            HANDLE_TYPE_ROOM,
            [self._activity_id],
            reply_handler=self.__got_handles_cb,
            error_handler=self.__error_handler_cb,
            dbus_interface=CONNECTION)

    def __got_handles_cb(self, handles):
        logging.debug('__got_handles_cb %r', handles)
        self.room_handle = handles[0]

        self._join_command = _JoinCommand(self._connection, self.room_handle)
        self._join_command.connect('finished', self.__joined_cb)
        self._join_command.run()

    def __joined_cb(self, join_command, error):
        _logger.debug('%r: Join finished %r', self, error)
        if error is not None:
            self._finished = True
            self.emit('finished', error)
            return

        self.text_channel = join_command.text_channel
        self.tubes_channel = join_command.tubes_channel

        self._connection.AddActivity(
            self._activity_id,
            self.room_handle,
            reply_handler=self.__added_activity_cb,
            error_handler=self.__error_handler_cb,
            dbus_interface=CONN_INTERFACE_BUDDY_INFO)

    def __added_activity_cb(self):
        self._finished = True
        self.emit('finished', None)

    def __error_handler_cb(self, error):
        self._finished = True
        self.emit('finished', error)

class _JoinCommand(_BaseCommand):
    def __init__(self, connection, room_handle):
        _BaseCommand.__init__(self)

        self._connection = connection
        self._room_handle = room_handle
        self._finished = False
        self._text_channel_group_flags = None
        self.text_channel = None
        self.tubes_channel = None

    def run(self):
        if self._finished:
            raise RuntimeError('This command has already finished')

        self._connection.RequestChannel(CHANNEL_TYPE_TEXT,
            HANDLE_TYPE_ROOM, self._room_handle, True,
            reply_handler=self.__create_text_channel_cb,
            error_handler=self.__error_handler_cb,
            dbus_interface=CONNECTION)

        self._connection.RequestChannel(CHANNEL_TYPE_TUBES,
            HANDLE_TYPE_ROOM, self._room_handle, True,
            reply_handler=self.__create_tubes_channel_cb,
            error_handler=self.__error_handler_cb,
            dbus_interface=CONNECTION)

    def __create_text_channel_cb(self, channel_path):
        Channel(self._connection.requested_bus_name, channel_path,
                ready_handler=self.__text_channel_ready_cb)

    def __create_tubes_channel_cb(self, channel_path):
        Channel(self._connection.requested_bus_name, channel_path,
                ready_handler=self.__tubes_channel_ready_cb)

    def __error_handler_cb(self, error):
        self._finished = True
        self.emit('finished', error)

    def __tubes_channel_ready_cb(self, channel):
        _logger.debug('%r: Tubes channel %r is ready', self, channel)
        self.tubes_channel = channel
        self._tubes_ready()

    def __text_channel_ready_cb(self, channel):
        _logger.debug('%r: Text channel %r is ready', self, channel)
        self.text_channel = channel
        self._tubes_ready()

    def _tubes_ready(self):
        if self.text_channel is None or \
            self.tubes_channel is None:
            return

        _logger.debug('%r: finished setting up tubes', self)

        self._add_self_to_channel()

    def __text_channel_group_flags_changed_cb(self, added, removed):
        _logger.debug('__text_channel_group_flags_changed_cb %r %r', added, removed)
        self._text_channel_group_flags |= added
        self._text_channel_group_flags &= ~removed

    def _add_self_to_channel(self):
        _logger.info('KILL_PS Connect to the Closed signal of the text channel')

        # FIXME: cope with non-Group channels here if we want to support
        # non-OLPC-compatible IMs

        group = self.text_channel[CHANNEL_INTERFACE_GROUP]

        def got_all_members(members, local_pending, remote_pending):
            _logger.debug('got_all_members local_pending %r members %r', members, local_pending)
            if members:
                self.__text_channel_members_changed_cb('', members, (),
                                                       (), (), 0, 0)

            _logger.info('KILL_PS Check that we pass the right self handle depending on the channel flags')

            if self._self_handle in members:
                _logger.debug('%r: I am already in the room', self)
                assert self._finished  # set by _text_channel_members_changed_cb
            else:
                _logger.debug('%r: Not yet in the room - entering', self)
                group.AddMembers([self._self_handle], '',
                    reply_handler=lambda: None,
                    error_handler=lambda e: self._join_failed_cb(e,
                        'got_all_members AddMembers'))

        def got_group_flags(flags):
            self._text_channel_group_flags = flags
            # by the time we hook this, we need to know the group flags
            group.connect_to_signal('MembersChanged',
                                    self.__text_channel_members_changed_cb)

            # bootstrap by getting the current state. This is where we find
            # out whether anyone was lying to us in their PEP info
            group.GetAllMembers(reply_handler=got_all_members,
                                error_handler=self.__error_handler_cb)

        def got_self_handle(self_handle):
            self._self_handle = self_handle
            group.connect_to_signal('GroupFlagsChanged',
                                    self.__text_channel_group_flags_changed_cb)
            group.GetGroupFlags(reply_handler=got_group_flags,
                                error_handler=self.__error_handler_cb)

        group.GetSelfHandle(reply_handler=got_self_handle,
                            error_handler=self.__error_handler_cb)

    def __text_channel_members_changed_cb(self, message, added, removed,
                                          local_pending, remote_pending,
                                          actor, reason):
        _logger.debug('__text_channel_members_changed_cb added %r', added)
        if self._self_handle in added:
            logging.info('KILL_PS Set the channel properties')
            self._finished = True
            self.emit('finished', None)

        return

        #_logger.debug('Activity %r text channel %u currently has %r',
        #              self, self._room_handle, self._handle_to_buddy)
        _logger.debug('Text channel %u members changed: + %r, - %r, LP %r, '
                      'RP %r, message %r, actor %r, reason %r', self._room_handle,
                      added, removed, local_pending, remote_pending,
                      message, actor, reason)
        # Note: D-Bus calls this with list arguments, but after GetMembers()
        # we call it with set and tuple arguments; we cope with any iterable.
        """
        if (self._text_channel_group_flags &
            CHANNEL_GROUP_FLAG_CHANNEL_SPECIFIC_HANDLES):
            _logger.debug('This channel has channel-specific handles')
            map_chan = self._text_channel
        else:
            # we have global handles here
            _logger.debug('This channel has global handles')
            map_chan = None

        # Disregard any who are already there - however, if we're joining
        # the channel, this will still consider everyone to have been added,
        # because _handle_to_buddy was cleared. That's necessary, so we get
        # the handle-to-buddy mapping for everyone.
        added = set(added)
        added -= frozenset(self._handle_to_buddy.iterkeys())
        _logger.debug('After filtering for no-ops, we want to add %r', added)
        added_buddies = self._ps.map_handles_to_buddies(self._tp,
                                                        map_chan,
                                                        added)
        for handle, buddy in added_buddies.iteritems():
            self._handle_to_buddy[handle] = buddy
            self._buddy_to_handle[buddy] = handle
        self._add_buddies(added_buddies.itervalues())

        self._claimed_buddies |= set(added_buddies.itervalues())

        # we treat all pending members as if they weren't there
        removed = set(removed)
        removed |= set(local_pending)
        removed |= set(remote_pending)
        # disregard any who aren't already there
        removed &= frozenset(self._handle_to_buddy.iterkeys())

        _logger.debug('After filtering for no-ops, we want to remove %r',
                      removed)
        removed_buddies = set()
        for handle in removed:
            buddy = self._handle_to_buddy.pop(handle, None)
            self._buddy_to_handle.pop(buddy)
            removed_buddies.add(buddy)
        # If we're not in the room yet, the "removal" may be spurious -
        # Gabble removes the inviter from members at the same time it adds
        # us to local-pending. We'll catch up anyway when we join the room and
        # do the apparent<->reality sync, so just don't remove anyone until
        # we've joined.
        if self._joined:
            self._remove_buddies(removed_buddies)

        # if we were among those removed, we'll have to start believing
        # the spoofable PEP-based activity tracking again.
        if self._self_handle not in self._handle_to_buddy and self._joined:
            self._text_channel_closed_cb()
        """
        self._handle_to_buddy[self._self_handle] = None
        if self._self_handle in self._handle_to_buddy and not self._joined:
            # We've just joined
            self._joined = True
            """
            _logger.debug('Syncing activity %r buddy list %r with reality %r',
                          self, self._buddies, self._handle_to_buddy)
            real_buddies = set(self._handle_to_buddy.itervalues())
            added_buddies = real_buddies - self._buddies
            if added_buddies:
                _logger.debug('... %r are here although they claimed not',
                              added_buddies)
            removed_buddies = self._buddies - real_buddies
            _logger.debug('... %r claimed to be here but are not',
                          removed_buddies)
            self._add_buddies(added_buddies)
            self._remove_buddies(removed_buddies)

            # Leave if the activity crashes
            if self._activity_unique_name is not None:
                _logger.debug('Watching unique name %s',
                              self._activity_unique_name)
                self._activity_unique_name_watch = dbus.Bus().watch_name_owner(
                    self._activity_unique_name, self._activity_unique_name_cb)
            """
            # Finish the Join process
            if PROPERTIES_INTERFACE not in self.text_channel:
                self.__join_activity_channel_props_listed_cb(())
            else:
                self.text_channel[PROPERTIES_INTERFACE].ListProperties(
                    reply_handler=self.__join_activity_channel_props_listed_cb,
                    error_handler=lambda e: self._join_failed_cb(e,
                        'Activity._text_channel_members_changed_cb'))

    def __join_activity_channel_props_listed_cb(self, prop_specs):
        # FIXME: invite-only ought to be set on private activities; but
        # since only the owner can change invite-only, that would break
        # activity scope changes.
        props = {
            'anonymous': False,   # otherwise buddy resolution breaks
            'invite-only': False, # anyone who knows about the channel can join
            'invite-restricted': False,     # so non-owners can invite others
            'persistent': False,  # vanish when there are no members
            'private': True,      # don't appear in server room lists
        }
        props_to_set = []
        for ident, name, sig, flags in prop_specs:
            value = props.pop(name, None)
            if value is not None:
                if flags & PROPERTY_FLAG_WRITE:
                    props_to_set.append((ident, value))
                # FIXME: else error, but only if we're creating the room?
        # FIXME: if props is nonempty, then we want to set props that aren't
        # supported here - raise an error?

        if props_to_set:
            self.text_channel[PROPERTIES_INTERFACE].SetProperties(
                props_to_set, reply_handler=self._joined_cb,
                error_handler=lambda e: self._join_failed_cb(e, 
                    'Activity._join_activity_channel_props_listed_cb'))
        else:
            self._joined_cb()

