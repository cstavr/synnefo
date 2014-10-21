Upgrade to Synnefo v0.16
^^^^^^^^^^^^^^^^^^^^^^^^

Introduction
============

Starting with version 0.16, we introduce Archipelago as the new storage backend
for the Pithos Service. Archipelago will act as a storage abstraction layer
between Pithos and NFS, RADOS or any other storage backend driver that
Archipelago supports. In order to use the Pithos Service you must install
Archipelago on the node that runs the Pithos and Cyclades workers.
Additionally, you must install Archipelago on the Ganeti nodes and upgrade
snf-image to version 0.16.2 since this is the first version that supports
Archipelago.

Until now the Pithos mapfile was a simple file containing a list of hashes that
make up the stored file in a Pithos container. After this consolidation the
Pithos mapfile had to be converted to an Archipelago mapfile. An Archipelago
mapfile is an updated version of the Pithos mapfile, intended to supersede it.

More info about the new mapfile you can find in Archipelago documentation.


Current state regarding ownerships and permissions
==================================================

All synnefo components in v0.15 run as ``www-data:www-data``. Pithos
uses either an NFS of a RADOS backend to store its mapfiles and actual
data. In case of NFS the exported directory is under
``/srv/pithos/data`` and owned by ``www-data:www-data`` with 640
permissions.

Archipelago is optional. If used, it is installed only in Ganeti nodes.
In order to support image cloning, it must access the NFS too.
snf-image uses pithcat to obtain the image data directly from NFS. Both
Archipelago and pithcat run as ``root:root`` so there are no
restrictions.

The latest Synnefo version introduces new dedicated system user and
group: ``synnefo``. On the other hand the latest Archipelago version can
run as non root and introduces the ``archipelago`` system user and
group. As described above, Archipelago becomes now the Pithos backend
and NFS will be accessed only by Archipelago.

In order this transition to be seamless (with minimum downtime) certain
steps must be followed regarding NFS ownerships and perrmissions.
Basically the problem is that any recursive action (chown/chmod -R) on
a production setup with many TB might take lot of time.

At the end of the day Synnefo (Cyclades, Pithos, Astakos, etc) will run
as ``synnefo:synnefo`` while Archipelago will run as
``archipelago:synnefo``. The NFS (if any) will be owned by
``archipelago:synnefo`` with 2660 permissions.


Upgrade Steps
=============

The upgrade to v0.16 consists of the following steps:

0. Upgrade snf-image on all Ganeti nodes

1. Upgrade / Install Archipelago on all nodes

2. Ensure intermediate state

3. Bring down services and backup databases

4. Upgrade packages, migrate the databases and configure settings

5. Inspect and adjust resource limits

6. Tweak Gunicorn settings on Pithos and Cyclades node

7. Bring up all services

8. Add unique names to disks of all Ganeti instances


.. warning::

    It is strongly suggested that you keep separate database backups
    for each service after the completion of each step.

0. Upgrade snf-image on all Ganeti nodes
========================================

On all Ganeti VM-capable nodes install the latest snf-image package
(v0.16.2). You should set the ``PITHCAT_UMASK`` setting of snf-image to
``007``. On the file ``/etc/default/snf-image`` uncomment or create the
relevant setting and set its value.


1. Upgrade / Install Archipelago on all nodes
=============================================

Up until now Archipelago was optional. So, your setup, either has no
Archipelago installation or has Archipelago v0.3.5 installed and
configured in all VM-capable nodes. Depending on your case refer to:

 * `Archipelago installation guide <https://www.synnefo.org/docs/archipelago/latest/install-guide.html>`_
 * `Archipelago upgrade notes <https://www.synnefo.org/docs/archipelago/latest/upgrades/upgrade-0.4.html>`_

Archipelago does not start automatically after installation.  Do not start it
manually until it is configured properly.

At this point there are some things to be made regarding ownerships
and permissions. At the end of the day Archipelago will run as
``archipelago:synnefo``.

Currently the Pithos data (i.e. ``/srv/archip`` or ``/srv/pithos/data``
depending on your setup) are owned by ``www-data:www-data`` with 640
permissions. Those data should be accessible by Pithos (i.e. gunicorn)
**and** Archipelago.

To ensure seamless transition we do the following:

* **Adjust system users and groups**

  NFS expects the user and group ID of the owners of the exported directory
  to be common across all nodes. Here we create the ``synnefo`` group in
  advance and modify the ``archipelago`` user and group properly.
  We assume here that ids 200 and 300 are available everywhere. On all nodes
  that we have already installed the new archipelago package, run:

  .. code-block:: console

      # usermod --uid 200 archipelago
      # groupmod --gid 200 archipelago
      # addgroup --system --gid 300 synnefo

* **Adjust Pithos umask setting**

  On the Pithos node, edit the file
  ``/etc/synnefo/20-snf-pithos-app-settings.conf`` and uncomment or add the
  ``PITHOS_BACKEND_BLOCK_UMASK`` setting and set it to value ``0o007``.

  Then perform a gunicorn restart on both nodes:

  .. code-block:: console

      # service gunicorn restart

  This way, all files and directories created by Pithos will be writable by the
  group Pithos gunicorn worker runs as (i.e. ``www-data``).

* **Change Pithos data group permissions**

  Ensure that every file and folder under Pithos data directory has correct
  permissions.

  .. code-block:: console

      # find /srv/pithos/data -type d -exec chmod g+rwxs '{}' \;
      # find /srv/pithos/data -type f -exec chmod g+rw '{}' \;

  This way, we prepare NFS to be fully accessible either via
  the user or the group.

* **Change gunicorn group**

  On the Pithos node, edit the file ``/etc/gunicorn.d/synnefo`` and set
  ``group`` to ``synnefo``. Then change the ownership of all
  configuration and log files:

  .. code-block:: console

     # chgrp -R synnefo /etc/synnefo
     # /etc/init.d/gunicorn restart

  This way, Pithos is able to access NFS via gunicorn user
  (``www-data``). We prepare Pithos to be able to access the ``synnefo``
  group.

* **Change Pithos data group owner**

  Make ``synnefo`` group the group owner of every file under the Pithos data
  directory.

  .. code-block:: console

      # chgrp archipelago /srv/pithos/data
      # find /srv/pithos/data -type d -exec chgrp archipelago '{}' \;
      # find /srv/pithos/data -type f -exec chgrp archipelago '{}' \;

  From now on, every file or directory created under the Pithos data directory
  will belong to the ``synnefo`` group because of the directory SET_GUID bit
  that we set on a previous step. Plus the ``synnefo`` group will have
  full read/write access because of the adjusted Pithos umask setting.

* **Make archipelago run as synnefo group**

  Change the Archipelago configuration on all nodes, to run as
  ``archipelago``:``synnefo``, since it no longer requires root
  priviledges. For each Archipelago node:

  * Stop Archipelago

    .. code-block:: console

      # archipelago stop

  * Change the ``USER`` and ``GROUP`` configuration option to ``archipelago``
    and ``synnefo`` respectively. The configuration file is located under
    ``/etc/archipelago/archipelago.conf``


  * Start Archipelago

    .. code-block:: console

      # archipelago start


2. Ensure intermediate state
============================

Pithos now runs as ``www-data:synnefo`` so any file created in the
exported directory will be ``www-data:synnefo`` with 660
permissions. Archipelago runs as ``archipelago:synnefo`` so it can
access NFS via the ``synnefo`` group. NFS (``blocks``, ``maps``,
``locks`` under ``/srv/pithos/data`` or ``/srv/archip``) will be owned by
``www-data:synnefo`` with 2660 permissions.


3. Bring web services down, backup databases
============================================

1. All web services must be brought down so that the database maintains a
   predictable and consistent state during the migration process::

    $ service gunicorn stop
    $ service snf-dispatcher stop
    $ service snf-ganeti-eventd stop

2. Backup databases for recovery to a pre-migration state.

3. Keep the database servers running during the migration process.


4. Upgrade Synnefo and configure settings
=========================================

4.1 Install the new versions of packages
----------------------------------------

::

    astakos.host$ apt-get install \
                            python-objpool \
                            snf-common \
                            python-astakosclient \
                            snf-django-lib \
                            snf-webproject \
                            snf-branding \
                            snf-astakos-app

    cyclades.host$ apt-get install \
                            python-objpool \
                            snf-common \
                            python-astakosclient \
                            snf-django-lib \
                            snf-webproject \
                            snf-branding \
                            snf-pithos-backend \
                            snf-cyclades-app

    pithos.host$ apt-get install \
                            python-objpool \
                            snf-common \
                            python-astakosclient \
                            snf-django-lib \
                            snf-webproject \
                            snf-branding \
                            snf-pithos-backend \
                            snf-pithos-app \
                            snf-pithos-webclient

    ganeti.node$ apt-get install \
                            python-objpool \
                            snf-common \
                            snf-cyclades-gtools \
                            snf-pithos-backend \
                            snf-network \
                            snf-image

.. note::

   Make sure ``snf-webproject`` has the same version with snf-common

.. note::

    Installing the packages will cause services to start. Make sure you bring
    them down again (at least ``gunicorn``, ``snf-dispatcher``)

.. note::

    If you are using qemu-kvm from wheezy-backports, note that qemu-kvm package
    2.1+dfsg-2~bpo70+2 has a bug that is triggered by snf-image. Check
    `snf-image installation <https://www.synnefo.org/docs/synnefo/latest/install-guide-debian.html#installation>`_ for
    a workaround.


The new snf-common package creates the ``synnefo`` system user and group.
We have already added the ``synnefo`` group in advance and made
gunicorn run as ``www-data:synnefo``.

To be able to talk with archipelago we must
run Archipelago as ``archipelago:synnefo`` on the Cyclades and Pithos
nodes.

Verify that ``USER`` and ``GROUP`` settings in
``/etc/archipelago/archipelago.conf`` are ``archipelago`` and ``synnefo``
respectively.

4.2 Sync and migrate the database
---------------------------------

.. note::

   If you are asked about stale content types during the migration process,
   answer 'no' and let the migration finish.

::

    astakos-host$ snf-manage syncdb
    astakos-host$ snf-manage migrate

    cyclades-host$ snf-manage syncdb
    cyclades-host$ snf-manage migrate

    pithos-host$ pithos-migrate upgrade head


4.3 Configure snf-vncauthproxy
------------------------------

Synnefo 0.16 replaces the Java VNC client with an HTML5 Websocket client and
the Cyclades UI will always request secure Websocket connections. You should,
therefore, provide snf-vncauthproxy with SSL certificates signed by a trusted
CA. You can either copy them to `/var/lib/vncauthproxy/{cert,key}.pem` or
inform vncauthproxy about the location of the certificates (via the
`DAEMON_OPTS` setting in `/etc/default/vncauthproxy`).

::

    DAEMON_OPTS="--pid-file=$PIDFILE --cert-file=<path_to_cert> --key-file=<path_to_key>"

Both files should be readable by the `vncauthproxy` user or group.

.. note::

    At the moment, the certificates should be issued to the FQDN of the
    Cyclades worker.

For more information on how to setup snf-vncauthproxy check the
snf-vncauthproxy `documentation <https://www.synnefo.org/docs/snf-vncauthproxy/latest/index.html#usage-with-synnefo>`_
and `upgrade notes <https://www.synnefo.org/docs/snf-vncauthproxy/latest/upgrade/upgrade-1.6.html>`_.



5. Inspect and adjust resource limits
=====================================

Synnefo 0.16 brings significant changes at the project mechanism. Projects
are now viewed as a source of finite resources, instead of a means to
accumulate quota. They are the single source of resources, and quota are now
managed at a project/member level.

System-provided quota are now handled through special purpose
user-specific *system projects*, identified with the same UUID as the user.
These have been created during the database migration process. They are
included in the project listing with::

  snf-manage project-list --system-projects

All projects must specify quota limits for all registered resources. Default
values have been set for all resources, listed with::

  astakos-host$ snf-manage resource-list

Column `system_default` (previously known as `default_quota`) provides the
skeleton for the quota limits of user-specific system projects. Column
`project_default` is new and acts as skeleton for `applied` (non-system)
projects (i.e., for resources not specified in a project application).
Project defaults have been initialized during migration based on the system
default values: they have been set to `inf` if `system_default` is also `inf`,
otherwise set to zero.

This default, affecting all future projects, can be modified with::

  astakos-host$ snf-manage resource-modify <name> --project-default <value>

Till now a project definition contained one quota limit per resource: the
maximum that a member can get from the project. A new limit is introduced:
the grand maximum a project can provide to its members. This new project
limit is initialized during migration as `max members * member limit` (if
`max members` is not set, the double of current active members is assumed).

Existing projects can now be modified directly through the command line. In
order to change a project's resource limits, run::

  astakos-host$ snf-manage project-modify <project_uuid> --limit <resource_name> <member_limit> <project_limit>

With the new mechanism, when a new resource is allocated (e.g., a VM or a
Pithos container is created), it is also associated with a project besides
its owner. The migration process has associated existing resources with
their owner's system project. Note that users who had made use of projects to
increase their quota may end up overlimit on some resources of their system
projects and will need to *reassign* some of their reserved resources to
another project in order to overcome this restriction.


6. Tweak Gunicorn settings on Pithos and Cyclades node
======================================================

For Gunicorn the configuration file is located on ``/etc/gunicorn.d/synnefo``
where we need to change:

* Let Gunicorn run as ``synnefo``:``synnefo``. Set ``user`` option to
  ``synnefo`` and ``group`` option to ``synnefo`` (Archipelago will run
  as ``synnefo``:``synnefo``).

On the Pithos and Cyclades node you also have to set the following:

* ``--config=/etc/synnefo/gunicorn-hooks/gunicorn-archipelago.py``


.. warning::

    If you have already installed Synnefo v0.16rc1 or v0.16rc2 you
    should replace ``pithos.conf.py`` with ``gunicorn-archipelago.py`` located
    under ``/etc/synnefo/gunicorn-hooks`` directory. Afterwards you
    can freely delete  ``pithos.conf.py`` conf file.


Then, on both nodes we must check and if needed, manually change group
ownership of the following directories to the group specified above:

* ``/var/log/gunicorn/`` directory
* ``/etc/synnefo/`` directory and all the files inside it.

.. code-block:: console

    # chgrp synnefo /var/log/gunicorn/
    # chgrp -R synnefo /etc/synnefo/

Also, on the Cyclades node, the ``snf-dispatcher`` must run as
``synnefo``:``synnefo``. Verify that the ``SNF_USER`` setting in
``/etc/default/snf-dispatcher`` is:

.. code-block:: console

	SNF_USER="synnefo:synnefo"

Change ownership of ``/var/log/synnefo`` so that snf-dispatcher can
access it:

.. code-block:: console

   # chown -R synnefo:synnefo /var/log/synnefo


Since now each file created by Synnefo will go through Archipelago
it will be created as ``archipelago:synnefo``. We can lazily chown
all files under the exported directory to ``archipelago:synnefo``.

.. code-block:: console

  # chown -R archipelago:synnefo /srv/pithos/data


7. Bring all services up
========================

After the upgrade is finished, we bring up all services:

.. code-block:: console

    astakos.host  # service gunicorn start
    cyclades.host # service gunicorn start

    pithos.host   # service gunicorn start

    cyclades.host # service snf-dispatcher start


8. Add unique names to disks of all Ganeti instances
=====================================================

Synnefo 0.16 introduces the Volume service which can handle multiple disks
per Ganeti instance. Synnefo assigns a unique name to each Ganeti disk and
refers to it by that unique name. After upgrading to v0.16, Synnefo must
assign names to all existing disks. This can be easily performed with a helper
script that is shipped with version 0.16:

.. code-block:: console

 cyclades.host$ /usr/lib/synnefo/tools/add_unique_name_to_disks
