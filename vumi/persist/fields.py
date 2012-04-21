# -*- test-case-name: vumi.persist.tests.test_fields -*-

"""Field types for Vumi's persistence models."""

from datetime import datetime

from vumi.message import VUMI_DATE_FORMAT


class ValidationError(Exception):
    """Raised when a value assigned to a field is invalid."""


class FieldDescriptor(object):
    """Property for getting and setting fields."""

    def __init__(self, key, field):
        self.key = key
        self.field = field

    def setup(self, cls):
        self.cls = cls

    def validate(self, value):
        self.field.validate(value)

    def set_value(self, modelobj, value):
        """Set the value associated with this descriptor."""
        modelobj._riak_object._data[self.key] = self.field.to_riak(value)

    def get_value(self, modelobj):
        """Get the value associated with this descriptor."""
        return self.field.from_riak(modelobj._riak_object._data[self.key])

    def __get__(self, instance, owner):
        if instance is None:
            return self.field
        return self.get_value(instance)

    def __set__(self, instance, value):
        if instance is None:
            raise AttributeError("Attribute %r of %r is not writable" %
                                 (self.key, self.cls))
        self.validate(value)
        self.set_value(instance, value)


class Field(object):
    """Base class for model attributes / fields."""

    descriptor_class = FieldDescriptor

    def get_descriptor(self, key):
        return self.descriptor_class(key, self)

    def validate(self, value):
        """Check whether a value is valid for this field.

        Raise an exception if it isn't.
        """
        pass

    def to_riak(self, value):
        """Convert a value to something storable by Riak."""
        return value

    def from_riak(self, value):
        """Convert a value from something stored by Riak to Python."""
        return value


class Integer(Field):
    """Field that accepts integers.

    :param integer min:
        Minimum allowed value (default is `None` which indicates no minimum).
    :param integer max:
        Maximum allowed value (default is `None` which indicates no maximum).
    """
    def __init__(self, min=None, max=None):
        self.min = min
        self.max = max

    def validate(self, value):
        if not isinstance(value, (int, long)):
            raise ValidationError("Value %r is not an integer." % (value,))
        if self.min is not None and value < self.min:
            raise ValidationError("Value %r too low (minimum value is %d)."
                                  % (value, self.min))
        if self.max is not None and value > self.max:
            raise ValidationError("Value %r too high (maximum value is %d)."
                                  % (value, self.max))


class Unicode(Field):
    """Field that accepts unicode strings."""
    def validate(self, value):
        if not isinstance(value, unicode):
            raise ValidationError("Value %r is not a unicode string."
                                  % (value,))


class Tag(Field):
    """Field that represents a Vumi tag."""
    def validate(self, value):
        if not isinstance(value, tuple) or len(value) != 2:
            raise ValidationError("Tags %r should be a (pool, tag_name)"
                                  " tuple" % (value,))

    def to_riak(self, value):
        return list(value)

    def from_riak(self, value):
        return tuple(value)


class VumiMessageDescriptor(FieldDescriptor):
    """Property for getting and setting fields."""

    def setup(self, cls):
        super(VumiMessageDescriptor, self).setup(cls)
        if self.field.prefix is None:
            self.prefix = "%s." % self.key
        else:
            self.prefix = self.field.prefix

    def _clear_keys(self, modelobj):
        for key in modelobj._riak_object._data.keys():
            if key.startswith(self.prefix):
                del modelobj._riak_object._data[key]

    def _timestamp_to_json(self, dt):
        return dt.strftime(VUMI_DATE_FORMAT)

    def _timestamp_from_json(self, value):
        return datetime.strptime(value, VUMI_DATE_FORMAT)

    def set_value(self, modelobj, msg):
        """Set the value associated with this descriptor."""
        self._clear_keys(modelobj)
        for key, value in msg.payload.iteritems():
            # TODO: timestamp as datetime in payload must die.
            if key == "timestamp":
                value = self._timestamp_to_json(value)
            full_key = "%s%s" % (self.prefix, key)
            modelobj._riak_object._data[full_key] = value

    def get_value(self, modelobj):
        """Get the value associated with this descriptor."""
        payload = {}
        for key, value in modelobj._riak_object._data.iteritems():
            if key.startswith(self.prefix):
                # TODO: timestamp as datetime in payload must die.
                if key == "timestamp":
                    value = self._timestamp_from_json(value)
                payload[key[len(self.prefix):]] = value
        return self.field.message_class(**payload)


class VumiMessage(Field):
    """Field that represent a Vumi message.

    :param class message_class:
        The class of the message objects being stored.
        Usually one of Message, TransportUserMessage or TransportEvent.
    :param string prefix:
        The prefix to use when storing message payload keys in Riak. Default is
        the name of the field followed by a dot ('.').
    """
    descriptor_class = VumiMessageDescriptor

    def __init__(self, message_class, prefix=None):
        self.message_class = message_class
        self.prefix = prefix

    def validate(self, value):
        if not isinstance(value, self.message_class):
            raise ValidationError("Message %r should be an instance of %r"
                                  % (value, self.message_class))


class FieldWithSubtype(Field):
    """Base class for a field that is a collection of other fields of a
    single type.

    :param Field field_type:
        The field specification for the dynamic values. Default is Unicode().
    """
    def __init__(self, field_type=None):
        if field_type is None:
            field_type = Unicode()
        if field_type.descriptor_class is not FieldDescriptor:
            raise RuntimeError("Dynamic fields only supports fields that"
                               " that use the basic FieldDescriptor class")
        self.field_type = field_type

    def validate(self, value):
        self.field_type.validate(value)

    def to_riak(self, value):
        return self.field_type.to_riak(value)

    def from_riak(self, value):
        return self.field_type.from_riak(value)


class DynamicDescriptor(FieldDescriptor):
    """A field descriptor for dynamic fields."""
    def setup(self, cls):
        super(DynamicDescriptor, self).setup(cls)
        if self.field.prefix is None:
            self.prefix = "%s." % self.key
        else:
            self.prefix = self.field.prefix

    def get_value(self, modelobj):
        return DynamicProxy(self, modelobj)

    def set_value(self, modelobj, value):
        raise RuntimeError("DynamicDescriptors should never be assigned to.")

    def get_dynamic_value(self, modelobj, dynamic_key):
        key = self.prefix + dynamic_key
        return self.field.from_riak(modelobj._riak_object._data[key])

    def set_dynamic_value(self, modelobj, dynamic_key, value):
        self.field.validate(value)
        key = self.prefix + dynamic_key
        modelobj._riak_object._data[key] = self.field.to_riak(value)


class DynamicProxy(object):
    def __init__(self, descriptor, modelobj):
        self.__dict__['_descriptor_modelobj_'] = (descriptor, modelobj)

    def __getattr__(self, key):
        descriptor, modelobj = self._descriptor_modelobj_
        return descriptor.get_dynamic_value(modelobj, key)

    def __setattr__(self, key, value):
        descriptor, modelobj = self._descriptor_modelobj_
        descriptor.set_dynamic_value(modelobj, key, value)


class Dynamic(FieldWithSubtype):
    """A field that allows sub-fields to be added dynamically.

    :param Field field_type:
        The field specification for the dynamic values. Default is Unicode().
    :param string prefix:
        The prefix to use when storing these values in Riak. Default is
        the name of the field followed by a dot ('.').
    """
    descriptor_class = DynamicDescriptor

    def __init__(self, field_type=None, prefix=None):
        super(Dynamic, self).__init__(field_type=field_type)
        self.prefix = prefix


class ListOfDescriptor(FieldDescriptor):
    """A field descriptor for ListOf fields."""

    def get_value(self, modelobj):
        return ListProxy(self, modelobj)

    def set_value(self, modelobj, value):
        raise RuntimeError("ListOfDescriptors should never be assigned to.")

    def _ensure_list(self, modelobj):
        if self.key not in modelobj._riak_object._data:
            modelobj._riak_object._data[self.key] = []

    def get_list_item(self, modelobj, list_idx):
        raw_item = modelobj._riak_object._data[self.key][list_idx]
        return self.field.from_riak(raw_item)

    def set_list_item(self, modelobj, list_idx, value):
        self.field.validate(value)
        raw_value = self.field.to_riak(value)
        self._ensure_list(modelobj)
        modelobj._riak_object._data[self.key][list_idx] = raw_value

    def del_list_item(self, modelobj, list_idx):
        del modelobj._riak_object._data[self.key][list_idx]

    def append_list_item(self, modelobj, value):
        self.field.validate(value)
        raw_value = self.field.to_riak(value)
        self._ensure_list(modelobj)
        modelobj._riak_object._data[self.key].append(raw_value)

    def extend_list(self, modelobj, values):
        for value in values:
            self.field.validate(value)
        raw_values = [self.field.to_riak(value) for value in values]
        self._ensure_list(modelobj)
        modelobj._riak_object._data[self.key].extend(raw_values)

    def iter_list(self, modelobj):
        for raw_value in modelobj._riak_object._data[self.key]:
            yield self.field.from_riak(raw_value)


class ListProxy(object):
    def __init__(self, descriptor, modelobj):
        self._descriptor = descriptor
        self._modelobj = modelobj

    def __getitem__(self, idx):
        return self._descriptor.get_list_item(self._modelobj, idx)

    def __setitem__(self, idx, value):
        self._descriptor.set_list_item(self._modelobj, idx, value)

    def __delitem__(self, idx):
        self._descriptor.del_list_item(self._modelobj, idx)

    def append(self, idx):
        self._descriptor.append_list_item(self._modelobj, idx)

    def extend(self, values):
        self._descriptor.extend_list(self._modelobj, values)

    def __iter__(self):
        return self._descriptor.iter_list(self._modelobj)


class ListOf(FieldWithSubtype):
    """A field that contains a list of values of some other type.

    :param Field field_type:
    The field specification for the dynamic values. Default is Unicode().
    """
    descriptor_class = ListOfDescriptor


class ForeignKeyDescriptor(FieldDescriptor):
    def setup(self, cls):
        super(ForeignKeyDescriptor, self).setup(cls)
        self.othercls = self.field.othercls
        if self.field.index is None:
            self.index_name = "%s_bin" % self.key
        else:
            self.index_name = self.field.index

        reverse_lookup_name = cls.__name__.lower() + "s"
        self.othercls.backlinks.declare_backlink(reverse_lookup_name,
                                                 self.reverse_lookup)

    def reverse_lookup(self, modelobj):
        manager = modelobj.manager
        mr = manager.riak_map_reduce()
        bucket = manager.bucket_prefix + self.cls.bucket
        mr.index(bucket, self.index_name, modelobj.key)
        return manager.run_map_reduce(mr, self.map_lookup_result)

    def map_lookup_result(self, manager, result):
        return self.cls.load(manager, result.get_key())

    def validate(self, value):
        if not isinstance(value, self.othercls):
            raise ValidationError("Field %r of %r requires a %r" %
                                  (self.key, self.cls, self.othercls))

    def get_value(self, modelobj):
        return ForeignKeyProxy(self, modelobj)

    def get_foreign_key(self, modelobj):
        indexes = modelobj._riak_object.get_indexes(self.index_name)
        if not indexes:
            return None
        key = indexes[0]
        return key

    def get_foreign_object(self, modelobj):
        key = self.get_foreign_key(modelobj)
        if key is None:
            return None
        return self.othercls.load(modelobj.manager, key)

    def set_value(self, modelobj, value):
        raise RuntimeError("ForeignKeyDescriptors should never be assigned"
                           " to.")

    def set_foreign_object(self, modelobj, otherobj):
        modelobj._riak_object.remove_index(self.index_name)
        if otherobj is not None:
            modelobj._riak_object.add_index(self.index_name, otherobj.key)


class ForeignKeyProxy(object):
    def __init__(self, descriptor, modelobj):
        self._descriptor = descriptor
        self._modelobj = modelobj

    @property
    def key(self):
        return self._descriptor.get_foreign_key(self._modelobj)

    def get(self):
        return self._descriptor.get_foreign_object(self._modelobj)

    def set(self, otherobj):
        self._descriptor.set_foreign_object(self._modelobj, otherobj)


class ForeignKey(Field):
    """A field that links to another class.

    :param Model othercls:
        The type of model linked to.
    :param string index:
        The name to use for the index. The default is the field name
        followed by _bin.
    """
    descriptor_class = ForeignKeyDescriptor

    def __init__(self, othercls, index=None):
        self.othercls = othercls
        self.index = index