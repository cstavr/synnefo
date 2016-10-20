# Copyright (C) 2010-2016 GRNET S.A. and individual contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging

from copy import deepcopy
from functools import wraps
from operator import itemgetter
from urllib import quote, unquote
from collections import namedtuple
from time import time, gmtime, strftime

from django.conf import settings
from django.utils.encoding import smart_unicode, smart_str

from snf_django.lib.api import faults
from synnefo.plankton.backend import (PLANKTON_META, OBJECT_AVAILABLE,
                                      OBJECT_UNAVAILABLE, OBJECT_ERROR)

from pithos.backends.util import PithosBackendPool
from pithos.backends.exceptions import (NotAllowedError, VersionNotExists,
                                        QuotaError, LimitExceeded)
from pithos.backends.modular import MAP_AVAILABLE, MAP_UNAVAILABLE, MAP_ERROR

Location = namedtuple("ObjectLocation", ["account", "container", "path"])

logger = logging.getLogger(__name__)


PLANKTON_DOMAIN = 'plankton'
PLANKTON_PREFIX = 'plankton:'
PROPERTY_PREFIX = 'property:'

SNAPSHOTS_CONTAINER = "snapshots"
SNAPSHOTS_TYPE = "application/octet-stream"

MAX_META_KEY_LENGTH = 128 - len(PLANKTON_DOMAIN) - len(PROPERTY_PREFIX)
MAX_META_VALUE_LENGTH = 256

PITHOSMAP_PREFIX = "pithosmap"

_pithos_backend_pool = None


def get_pithos_backend():
    global _pithos_backend_pool
    if _pithos_backend_pool is None:
        _pithos_backend_pool = PithosBackendPool(
            settings.PITHOS_BACKEND_POOL_SIZE,
            astakos_auth_url=settings.ASTAKOS_AUTH_URL,
            service_token=settings.CYCLADES_SERVICE_TOKEN,
            astakosclient_poolsize=settings.CYCLADES_ASTAKOSCLIENT_POOLSIZE,
            block_module=settings.PITHOS_BACKEND_BLOCK_MODULE,
            block_params=settings.PITHOS_BACKEND_BLOCK_KWARGS,
            db_connection=settings.BACKEND_DB_CONNECTION,
            archipelago_conf_file=settings.PITHOS_BACKEND_ARCHIPELAGO_CONF,
            xseg_pool_size=settings.PITHOS_BACKEND_XSEG_POOL_SIZE,
            map_check_interval=settings.PITHOS_BACKEND_MAP_CHECK_INTERVAL,
            resource_max_metadata=settings.PITHOS_RESOURCE_MAX_METADATA)
    return _pithos_backend_pool.pool_get()


def format_timestamp(t):
    return strftime('%Y-%m-%d %H:%M:%S', gmtime(t))


def handle_pithos_backend(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        backend = self.backend
        backend.pre_exec()
        commit = False
        try:
            ret = func(self, *args, **kwargs)
        except NotAllowedError:
            raise faults.Forbidden
        except (NameError, VersionNotExists):
            raise faults.ItemNotFound
        except (AssertionError, ValueError):
            raise faults.BadRequest
        except QuotaError:
            raise faults.OverLimit
        except LimitExceeded, e:
            raise faults.BadRequest(e.args[0])
        else:
            commit = True
        finally:
            backend.post_exec(commit)
        return ret
    return wrapper


MAP_TO_OBJ_STATES = {
    MAP_AVAILABLE: OBJECT_AVAILABLE,
    MAP_UNAVAILABLE: OBJECT_UNAVAILABLE,
    MAP_ERROR: OBJECT_ERROR
}

OBJ_TO_MAP_STATES = dict([(v, k) for k, v in MAP_TO_OBJ_STATES.items()])


class PithosPlanktonDriver(object):
    """A wrapper arround the pithos backend to simplify image handling."""

    def __init__(self):
        self.backend = get_pithos_backend()

    def close(self):
        """Close PithosBackend(return to pool)"""
        self.backend.close()

    @handle_pithos_backend
    def get_image(self, user, uuid, check_permissions=True):
        return self._get_image(user, uuid, check_permissions=check_permissions)

    def _get_image(self, user, uuid, check_permissions=True):
        location, metadata, permissions = \
            self.get_pithos_object(user, uuid,
                                   check_permissions=check_permissions)
        return image_to_dict(location, metadata, permissions)

    def add_property(self, user, uuid, key, value):
        return self.update_properties(user, uuid, {key: value}, replace=False)

    def remove_property(self, user, uuid, key):
        return self.update_properties(user, uuid, {key: ""}, replace=False)

    @handle_pithos_backend
    def update_properties(self, user, uuid, properties, replace=False):
        location, _, _p = self.get_pithos_object(user, uuid)
        properties = self._prefix_properties(properties)
        self._update_metadata(user, uuid, location, properties,
                              replace=replace)

    @staticmethod
    def _prefix_properties(properties):
        """Add property prefix to properties."""
        return dict([(PROPERTY_PREFIX + k, v) for k, v in properties.items()])

    @staticmethod
    def _unprefix_properties(properties):
        """Remove property prefix from properties."""
        return dict([(k.replace(PROPERTY_PREFIX, "", 1), v)
                     for k, v in properties.items()])

    @staticmethod
    def _prefix_metadata(metadata):
        """Add plankton prefix to metadata."""
        return dict([(PLANKTON_PREFIX + k, v) for k, v in metadata.items()])

    @staticmethod
    def _unprefix_metadata(metadata):
        """Remove plankton prefix from metadata."""
        return dict([(k.replace(PLANKTON_PREFIX, "", 1), v)
                     for k, v in metadata.items()])

    @handle_pithos_backend
    def update_metadata(self, user, uuid, metadata, replace=False):
        location, _m, permissions = self.get_pithos_object(user, uuid)

        is_public = metadata.pop("is_public", None)
        if is_public is not None:
            assert(isinstance(is_public, bool))
            read = set(permissions.get("read", []))
            if is_public and "*" not in read:
                read.add("*")
            elif not is_public and "*" in read:
                read.discard("*")
            permissions["read"] = list(read)
            self._update_permissions(user, uuid, location, permissions)

        # Each property is stored as a separate prefixed metadata
        meta = deepcopy(metadata)
        properties = meta.pop("properties", {})
        meta.update(self._prefix_properties(properties))

        self._update_metadata(user, uuid, location, metadata=meta,
                              replace=replace)

        return self._get_image(user, uuid)

    def _update_metadata(self, user, uuid, location, metadata, replace=False):
        prefixed = self._prefix_and_validate_metadata(metadata)

        account, container, path = location
        self.backend.update_object_meta(user, account, container, path,
                                        PLANKTON_DOMAIN, prefixed, replace)
        logger.debug("User '%s' updated image '%s', metadata: '%s'", user,
                     uuid, prefixed)

    def _prefix_and_validate_metadata(self, metadata):
        _prefixed_metadata = self._prefix_metadata(metadata)
        prefixed = {}
        for k, v in _prefixed_metadata.items():
            # Encode to UTF-8
            k, v = smart_unicode(k), smart_unicode(v)
            # Check the length of key/value
            if len(k) > MAX_META_KEY_LENGTH:
                raise faults.BadRequest('Metadata keys should be less than %s'
                                        ' characters' % MAX_META_KEY_LENGTH)
            if len(v) > MAX_META_VALUE_LENGTH:
                raise faults.BadRequest('Metadata values should be less than'
                                        ' %s characters.'
                                        % MAX_META_VALUE_LENGTH)
            prefixed[k] = v
        return prefixed

    def get_pithos_object(self, user, uuid, version=None,
                          check_permissions=True, check_image=True):
        """Get a Pithos object based on its UUID.

        If 'version' is not specified, the latest non-deleted version of this
        object will be retrieved.
        If 'check_permissions' is set to False, the Pithos backend will not
        check if the user has permissions to access this object.
        Finally, the 'check_image' option is used to check whether the Pithos
        object is an image or not.

        """
        if check_permissions:
            user = user
        else:
            user = None

        meta, permissions, path = self.backend.get_object_by_uuid(
            uuid=uuid, version=version, domain=PLANKTON_DOMAIN,
            user=user, check_permissions=check_permissions)
        account, container, path = path.split("/", 2)
        location = Location(account, container, path)

        if check_image and PLANKTON_PREFIX + "name" not in meta:
            # Check that object is an image by checking if it has an Image name
            # in Plankton metadata
            raise faults.ItemNotFound("Object '%s' does not exist." % uuid)

        return location, meta, permissions

    # Users and Permissions
    @handle_pithos_backend
    def add_member(self, user, uuid, member):
        assert(isinstance(member, basestring))
        location, _, permissions = self.get_pithos_object(user, uuid)
        read = set(permissions.get("read", []))
        if member not in read:
            read.add(member)
            permissions["read"] = list(read)
            self._update_permissions(user, uuid, location, permissions)

    @handle_pithos_backend
    def remove_member(self, user, uuid, member):
        assert(isinstance(member, basestring))
        location, _, permissions = self.get_pithos_object(user, uuid)
        read = set(permissions.get("read", []))
        if member in read:
            read.remove(member)
            permissions["read"] = list(read)
            self._update_permissions(user, uuid, location, permissions)

    @handle_pithos_backend
    def update_members(self, user, uuid, members):
        assert(isinstance(members, list))
        location, _, permissions = self.get_pithos_object(user, uuid)
        read = set(permissions.get("read", []))
        if "*" in read:  # Retain public permissions
            members.append("*")
        permissions["read"] = list(members)
        self._update_permissions(user, uuid, location, permissions)

    @handle_pithos_backend
    def list_members(self, user, uuid):
        location, _, permissions = self.get_pithos_object(user, uuid)
        return [member for member in permissions.get('read', [])
                if member != '*']

    def _update_permissions(self, user, uuid, location, permissions):
        account, container, path = location
        self.backend.update_object_permissions(user, account, container,
                                               path, permissions)
        logger.debug("User '%s' updated image '%s' permissions: '%s'",
                     user, uuid, permissions)

    @handle_pithos_backend
    def register(self, user, name, location, metadata):
        # Validate that metadata are allowed
        if "id" in metadata:
            raise faults.BadRequest("Passing an ID is not supported")
        store = metadata.pop("store", "pithos")
        if store != "pithos":
            raise faults.BadRequest("Invalid store '%s'. Only 'pithos' store"
                                    " is supported" % store)
        disk_format = metadata.setdefault("disk_format",
                                          settings.DEFAULT_DISK_FORMAT)
        if disk_format not in settings.ALLOWED_DISK_FORMATS:
            raise faults.BadRequest("Invalid disk format '%s'" % disk_format)
        container_format =\
            metadata.setdefault("container_format",
                                settings.DEFAULT_CONTAINER_FORMAT)
        if container_format not in settings.ALLOWED_CONTAINER_FORMATS:
            raise faults.BadRequest("Invalid container format '%s'" %
                                    container_format)

        try:
            account, container, path = split_url(location)
        except AssertionError:
            raise faults.BadRequest("Invalid location '%s'" % location)
        location = Location(account, container, path)
        meta = self.backend.get_object_meta(user, account, container,
                                            path, PLANKTON_DOMAIN, None)
        uuid = meta["uuid"]

        # Validate that 'size' and 'checksum'
        size = metadata.pop('size', int(meta['bytes']))
        if not isinstance(size, int) or int(size) != int(meta["bytes"]):
            raise faults.BadRequest("Invalid 'size' field")

        checksum = metadata.pop('checksum', meta['hash'])
        if not isinstance(checksum, basestring) or checksum != meta['hash']:
            raise faults.BadRequest("Invalid checksum field")

        users = [user]
        public = metadata.pop("is_public", False)
        if not isinstance(public, bool):
            raise faults.BadRequest("Invalid value for 'is_public' metadata")
        if public:
            users.append("*")
        permissions = {'read': users}
        self._update_permissions(user, uuid, location, permissions)

        # Each property is stored as a separate prefixed metadata
        meta = deepcopy(metadata)
        properties = meta.pop("properties", {})
        meta.update(self._prefix_properties(properties))
        # Add extra metadata
        meta["name"] = name
        meta['created_at'] = str(time())
        self._update_metadata(user, uuid, location, metadata=meta,
                              replace=False)

        logger.debug("User '%s' registered image '%s'('%s')", user,
                     uuid, location)
        return self._get_image(user, uuid)

    @handle_pithos_backend
    def unregister(self, user, uuid):
        """Unregister an Image.

        Unregister an Image by removing all the metadata in the Plankton
        domain. The Pithos file is not deleted.

        """
        location, _m, _p = self.get_pithos_object(user, uuid)
        self._update_metadata(user, uuid, location, metadata={}, replace=True)
        logger.debug("User '%s' unregistered image '%s'", user, uuid)

    # List functions
    def _list_images(self, user=None, filters=None, params=None,
                     check_permissions=True):
        filters = filters or {}

        # TODO: Use filters
        # # Fix keys
        # keys = [PLANKTON_PREFIX + 'name']
        # size_range = (None, None)
        # for key, val in filters.items():
        #     if key == 'size_min':
        #         size_range = (val, size_range[1])
        #     elif key == 'size_max':
        #         size_range = (size_range[0], val)
        #     else:
        #         keys.append('%s = %s' % (PLANKTON_PREFIX + key, val))
        _images = self.backend.get_domain_objects(
            domain=PLANKTON_DOMAIN, user=user,
            check_permissions=check_permissions)

        images = []
        for (location, metadata, permissions) in _images:
            location = Location(*location.split("/", 2))
            images.append(image_to_dict(location, metadata, permissions))

        if params is None:
            params = {}

        key = itemgetter(params.get('sort_key', 'created_at'))
        reverse = params.get('sort_dir', 'desc') == 'desc'
        images.sort(key=key, reverse=reverse)
        return images

    @handle_pithos_backend
    def list_images(self, user, filters=None, params=None,
                    check_permissions=True):
        return self._list_images(user=user, filters=filters,
                                 params=params,
                                 check_permissions=check_permissions)

    # Snapshots
    @handle_pithos_backend
    def register_snapshot(self, user, name, size, metadata, volume_id,
                          volume_snapshot_cnt):
        meta = deepcopy(metadata)
        properties = meta.pop("properties", {})
        meta.update(self._prefix_properties(properties))
        meta = self._prefix_and_validate_metadata(meta)

        # Generate mapfile name
        mapfile = "snf-snap-%s-%s" % (volume_id, volume_snapshot_cnt)

        uuid = self.backend.register_object_map(
            user=user,
            account=user,
            container=SNAPSHOTS_CONTAINER,
            name=name,
            mapfile=mapfile,
            size=size,
            domain=PLANKTON_DOMAIN,
            type=SNAPSHOTS_TYPE,
            meta=meta,
            replace_meta=True,
            permissions=None)

        return self._get_image(user, uuid)

    def list_snapshots(self, user, check_permissions=True):
        _snapshots = self.list_images(user,
                                      check_permissions=check_permissions)
        return [s for s in _snapshots if s["is_snapshot"]]

    @handle_pithos_backend
    def get_snapshot(self, user, uuid, check_permissions=True):
        snap = self._get_image(user, uuid,
                               check_permissions=check_permissions)
        if snap.get("is_snapshot", False) is False:
            raise faults.ItemNotFound("Snapshots '%s' does not exist" % uuid)
        return snap

    @handle_pithos_backend
    def delete_snapshot(self, user, uuid):
        self.backend.delete_by_uuid(user, uuid)

    @handle_pithos_backend
    def update_snapshot_state(self, uuid, state):
        state = OBJ_TO_MAP_STATES[state]
        self.backend.update_object_status(uuid, state=state)


def create_url(account, container, name):
    """Create a Pithos URL from the object info"""
    assert "/" not in account, "Invalid account"
    assert "/" not in container, "Invalid container"
    account = quote(smart_str(account, encoding="utf-8"))
    container = quote(smart_str(container, encoding="utf-8"))
    name = quote(smart_str(name, encoding="utf-8"))
    return "pithos://%s/%s/%s" % (account, container, name)


def split_url(url):
    """Get object info from the Pithos URL"""
    assert(isinstance(url, basestring))
    t = url.split('/', 4)
    assert t[0] == "pithos:", "Invalid url"
    assert len(t) == 5, "Invalid url"
    account, container, name = t[2:5]
    parse = lambda x: smart_unicode(unquote(x), encoding="utf-8")
    return parse(account), parse(container), parse(name)


def image_to_dict(location, metadata, permissions):
    """Render an image to a dictionary"""
    account, container, name = location

    image = {}
    image["id"] = metadata["uuid"]
    image["backend_id"] = metadata["mapfile"]
    image["backend_uri"] = "%s://%s/%s" % (PITHOSMAP_PREFIX,
                                           metadata["mapfile"],
                                           metadata["bytes"])
    image["checksum"] = metadata["hash"]
    image["location"] = create_url(account, container, name)
    image["size"] = metadata["bytes"]
    image['owner'] = account
    image["store"] = u"pithos"
    image["is_snapshot"] = metadata["is_snapshot"]
    image["version"] = metadata["version"]

    image["status"] = MAP_TO_OBJ_STATES.get(metadata.get("available"),
                                            "UNKNOWN")

    # Permissions
    users = list(permissions.get("read", []))
    image["is_public"] = "*" in users
    image["users"] = [u for u in users if u != "*"]
    # Timestamps
    updated_at = metadata["version_timestamp"]
    created_at = metadata.get("created_at", updated_at)
    image["created_at"] = format_timestamp(created_at)
    image["updated_at"] = format_timestamp(updated_at)
    if metadata.get("deleted", False):
        image["deleted_at"] = image["updated_at"]
    else:
        image["deleted_at"] = ""
    # Ganeti ID and job ID to be used for snapshot reconciliation
    image["backend_info"] = metadata.pop(PLANKTON_PREFIX + "backend_info",
                                         None)

    properties = {}
    for key, val in metadata.items():
        # Get plankton properties
        if key.startswith(PLANKTON_PREFIX):
            # Remove plankton prefix
            key = key.replace(PLANKTON_PREFIX, "")
            # Keep only those in plankton metadata
            if key in PLANKTON_META:
                if key != "created_at":
                    # created timestamp is return in 'created_at' field
                    image[key] = val
            elif key.startswith(PROPERTY_PREFIX):
                key = key.replace(PROPERTY_PREFIX, "")
                properties[key] = val

    image["properties"] = properties

    return image
