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

"""
The Plankton attributes are the following:
  - checksum: the 'hash' meta
  - container_format: stored as a user meta
  - created_at: the 'modified' meta of the first version
  - deleted_at: the timestamp of the last version
  - disk_format: stored as a user meta
  - id: the 'uuid' meta
  - is_public: True if there is a * entry for the read permission
  - location: generated based on the file's path
  - name: stored as a user meta
  - owner: the file's account
  - properties: stored as user meta prefixed with PROPERTY_PREFIX
  - size: the 'bytes' meta
  - store: is always 'pithos'
  - updated_at: the 'modified' meta
"""

import logging
from django.conf import settings
from django.utils import importlib

PLANKTON_META = ("container_format", "disk_format", "name",
                 "status", "created_at", "volume_id", "description",
                 "properties")

OBJECT_AVAILABLE = "AVAILABLE"
OBJECT_UNAVAILABLE = "UNAVAILABLE"
OBJECT_ERROR = "ERROR"
OBJECT_DELETED = "DELETED"

logger = logging.getLogger(__name__)


def import_driver(driver_str):
    mod_str, _sep, cls_str = driver_str.rpartition(".")
    mod = importlib.import_module(mod_str)
    try:
        return getattr(mod, cls_str)
    except (ImportError, AttributeError), e:
        raise ImportError("Cannot import Plankton driver: %s (%s)" %
                          (driver_str, e.message))


class PlanktonBackend(object):
    """Manages Images and Snapshots using a Plankton driver."""

    def __init__(self, user, driver_cls=None):
        """Initialize PlanktonBackend for the given user.

        Loads either the given driver or the one specified in the
        'PLANKTON_BACKEND_DRIVER' setting.
        """
        self.user = user
        self.driver_cls = driver_cls
        if self.driver_cls is None:
            self.driver_cls = settings.PLANKTON_BACKEND_DRIVER
        self.driver = import_driver(self.driver_cls)()

    def close(self):
        if self.driver:
            self.driver.close()
            self.driver = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        self.driver = None
        return False

    # Images
    def register(self, name, location, metadata):
        """Register object at given location as an image."""
        image = self.driver.register(self.user, name, location, metadata)
        logger.debug("User '%s' registered image '%s", image["id"])
        return image

    def unregister(self, uuid):
        self.driver.unregister(self.user, uuid)
        logger.debug("User '%s' unregistered image '%s'", self.user, uuid)

    def get_image(self, uuid, check_permissions=True):
        """Get an image by tis UUID."""
        return self.driver.get_image(self.user, uuid,
                                     check_permissions=check_permissions)

    def list_images(self, filters=None, params=None, check_permissions=True):
        """List all existing images."""
        return self.driver.list_images(self.user, filters, params,
                                       check_permissions=check_permissions)

    # Properties and metadata
    def add_property(self, uuid, key, value):
        """Add property to to an image."""
        return self.driver.update_property(self.user, uuid, key, value)

    def remove_property(self, uuid, key):
        """Remove property from an image."""
        return self.driver.remove_property(self.user, uuid, key)

    def update_properties(self, uuid, properties, replace=False):
        """Update the properties of an image."""
        self.driver.update_properties(self.user, uuid, properties,
                                      replace=replace)

    def update_metadata(self, uuid, metadata, replace=False):
        """Update the metadata of an image."""
        self.driver.update_metadata(self.user, uuid, metadata, replace=replace)

    # Users and Permissions
    def add_member(self, uuid, member):
        """Add member to an image/snapshot."""
        self.driver.add_member(self.user, uuid, member)

    def remove_member(self, uuid, member):
        """Remove member from an image/snapshot."""
        self.driver.remove_member(self.user, uuid, member)

    def update_members(self, uuid, members):
        """Update members of an image/snapshot."""
        self.driver.update_members(self.user, uuid, members)

    def list_members(self, uuid):
        """List members of an image/snapshot."""
        return self.driver.list_members(self.user, uuid)

    # Snapshots
    def register_snapshot(self, name, size, metadata, volume_id,
                          volume_snapshot_cnt):
        """Register given mapfile as a snapshot."""
        snapshot = self.driver.register_snapshot(self.user, name,
                                                 size, metadata, volume_id,
                                                 volume_snapshot_cnt)
        logger.debug("User '%s' create snapshot '%s'", self.user,
                     snapshot["id"])
        return snapshot

    def delete_snapshot(self, uuid):
        """Delete an existing snapshot."""
        self.driver.delete_snapshot(self.user, uuid)
        logger.debug("User '%s' deleted snapshot '%s'", self.user, uuid)

    def list_snapshots(self, check_permissions=True):
        """List all existing snapshots."""
        return self.driver.list_snapshots(self.user,
                                          check_permissions=check_permissions)

    def get_snapshot(self, uuid, check_permissions=True):
        """Get a snapshots by its UUID."""
        return self.driver.get_snapshot(self.user, uuid,
                                        check_permissions=check_permissions)

    def update_snapshot_state(self, uuid, state):
        """Update the state of a snapshot."""
        self.driver.update_snapshot_state(uuid, state)
