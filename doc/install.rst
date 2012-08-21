Installation
============

.. note:: Following this guide will give you a running Varda server suitable
    for a development environment. Deployment to a production server will
    probably deviate on some points (but shoudn't be done anyway since this
    is pre-alpha software).

.. note:: This guide assumes installation on a Debian (testing, or *wheezy*)
    system with Python 2.7.

Getting Varda server running consists of the following steps:

* `Installing a database server`_
* `Installing a message broker`_
* `Setting up a Python virtual environment`_
* `Creating initial configuration`_
* `Setting up the database`_
* `Running Varda server`_


.. _database:

Installing a database server
----------------------------

The recommended database server is PostreSQL, but MySQL will also work. You
might even get away with SQLite. Choose one of the three.


Option 1: PostgreSQL
^^^^^^^^^^^^^^^^^^^^

Install PostgreSQL and add a user for Varda server. Create two empty databases,
``varda`` and ``vardaresults``, both owned by the new user. For example::

    $ sudo aptitude install postgresql
    $ sudo -u postgres createuser --superuser $USER
    $ createuser --pwprompt --encrypted --no-adduser --no-createdb --no-createrole varda
    $ createdb --encoding=UNICODE --owner=varda varda
    $ createdb --encoding=UNICODE --owner=varda vardaresults

Also install some development libraries needed for building the psycopg2
Python package::

    $ sudo aptitude install python-dev libpq-dev


Option 2: MySQL
^^^^^^^^^^^^^^^

Example installation and setup of MySQL::

    $ sudo aptitude install mysql-server
    $ mysql -h localhost -u root -p
    > create database varda;
    > create database vardaresults;
    > grant all privileges on varda.* to varda@localhost identified by '*******';
    > grant all privileges on vardaresults.* to varda@localhost identified by '*******';

Install some development libraries needed for building the MySQL-python
package::

    $ sudo aptitutde install python-dev libmysqlclient-dev

Substitute ``MySQL-python`` for ``psycopg2`` in the ``requirements.txt``
before you use it in the :ref:`varda-virtualenv` section.


Option 3: SQLite
^^^^^^^^^^^^^^^^

I think you have all you need. You can remove the ``psycopg2`` line in
``requirements.txt``.


.. _broker:

Installing a message broker
---------------------------

A message broker is needed for communication between the server process and
worker processes. The recommended message broker is `Redis <http://redis.io>`_::

    $ sudo aptitude install redis-server

Alternatively, `RabbitMQ <http://www.rabbitmq.com/>`_ can be used as message
broker (prefarably add the APT repository `provided by RabbitMQ <http://www.rabbitmq.com/install-debian.html>`_).
Example::

    $ sudo apt-get install rabbitmq-server
    $ sudo rabbitmqctl add_user varda varda
    $ sudo rabbitmqctl add_vhost varda
    $ sudo rabbitmqctl set_permissions -p varda varda '.*' '.*' '.*'

As a third alternative, but not recommended, you can use your database server
as message broker as well.


.. _varda-virtualenv:

Setting up a Python virtual environment
---------------------------------------

It is recommended to run Varda server from a Python virtual environment, using
`virtualenv <http://www.virtualenv.org/>`_. Managing virtual environments is
easiest using `virtualenvwrapper <http://www.doughellmann.com/docs/virtualenvwrapper/>`_.

Install `pip <http://www.pip-installer.org/en/latest/index.html>`_, virtualenv,
and virtualenvwrapper::

    $ sudo easy_install pip
    $ sudo pip install virtualenv
    $ sudo pip install virtualenvwrapper
    $ mkdir ~/.virtualenvs

Add the following to your ``~/.bashrc`` and start a new shell::

    export WORKON_HOME=~/.virtualenvs
    if [ -f /usr/local/bin/virtualenvwrapper.sh ]; then
        source /usr/local/bin/virtualenvwrapper.sh
    fi
    export PIP_VIRTUALENV_BASE=$WORKON_HOME
    export PIP_REQUIRE_VIRTUALENV=true
    export PIP_RESPECT_VIRTUALENV=true

Create the environment for Varda server and install all required Python
packages::

    $ mkvirtualenv varda-server
    $ pip install -r requirements.txt

Now might be a good idea to run the unit tests::

    $ nosetests -v

The remainder of this guide assumes the virtual environment is activated.


.. _configuration:

Creating initial configuration
------------------------------

Varda server looks for its configuration in the file specified by the
``VARDA_SETTINGS`` environment variable. First create the file with your
configuration settings, for example::

    $ export VARDA_SETTINGS=~/varda-server/settings.py
    $ cat > $VARDA_SETTINGS
    ADMINS = ['martijn@vermaat.name']
    SQLALCHEMY_DATABASE_URI = 'postgresql://varda:varda@localhost/varda'
    CELERY_RESULT_BACKEND = 'database'
    CELERY_RESULT_DBURI = 'postgresql://varda:varda@localhost/vardaresults'
    BROKER_URL = 'redis://localhost:6379/0'

Some example settings can be found in ``varda/default_settings.py``.

Make sure to always have the ``VARDA_SETTINGS`` environment variable set when
invoking any component of Varda server. One way of doing this is adding the
above ``export`` command to your ``~/.bashrc``. Another is prefixing your
invocations with ``VARDA_SETTINGS=...``.


.. _database-setup:

Setting up the database
-----------------------

The following is an example Python session creating the database tables and
setting up users::

    from varda import create_app, db
    from varda.models import User

    app = create_app()
    app.test_request_context().push()

    db.drop_all()
    db.create_all()

    pietje = User('Pietje Puk', 'pietje', 'pi3tje', roles=['admin'])
    karel = User('Karel Koek', 'karel', 'k4rel', roles=['importer'])
    martijn = User('Martijn Vermaat', 'martijn', 'martijn',
                   roles=['admin', 'importer', 'annotator'])

    db.session.add(pietje)
    db.session.add(karel)
    db.session.add(martijn)
    db.session.commit()

.. _running:

Running Varda server
--------------------

Start a Celery worker node (only used for long-running tasks)::

    $ celery -A varda.worker.celery worker -l info

And start Varda server::

    $ ./runserver

You can now point your webbrowser to the URL that is printed and see a json-
encoded status page.