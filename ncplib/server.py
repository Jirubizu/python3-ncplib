"""
NCP server
==========

.. currentmodule:: ncplib

:mod:`ncplib` allows you to create a NCP server and respond to incoming :doc:`client` connections.


Overview
--------

Defining a connection handler
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A connection handler is a coroutine that starts whenever a new :doc:`client` connects to the server. The provided
:class:`Connection` allows you to receive incoming NCP commands as :class:`Field` instances.

.. code:: python

    async def client_connected(connection):
        pass

When the connection handler exits, the :class:`Connection` will automatically close.


Listening for an incoming packet
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

When writing a :doc:`server`, you most likely want to wait for the connected client to execute a command. Within your
``client_connected`` function, Listen for an incomining :term:`NCP field` using :meth:`Connection.recv`.

.. code:: python

    field = await connection.recv()

Alternatively, use the :class:`Connection` as an *async iterator* to loop over multiple :term:`NCP field` replies:

.. code:: python

    async for field in connection:
        pass

.. important::
    The *async for loop* will only terminate when the underlying connection closes.


Accessing field data
^^^^^^^^^^^^^^^^^^^^

The return value of :meth:`Connection.recv` is a :class:`Field`, representing a :term:`NCP field`.

Access information about the :term:`NCP field` and enclosing :term:`NCP packet`:

.. code:: python

    print(field.packet_type)
    print(field.name)

Access contained :term:`NCP parameters <NCP parameter>` using item access:

.. code:: python

    print(field["FCTR"])


Replying to the incoming field
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Send a reply to an incoming :class:`Field` using :meth:`Field.send`.

.. code:: python

    field.send(ACKN=1)


Putting it all together
^^^^^^^^^^^^^^^^^^^^^^^

A simple ``client_connected`` callback might like this:

.. code:: python

    async def client_connected(connection):
        async for field in connection:
            if field.packet_type == "DSPC" and field.name == "TIME":
                field.send(ACNK=1)
                # Do some more command processing here.
            else:
                field.send(ERRO="Unknown command", ERRC=400)
                break


Start the server
^^^^^^^^^^^^^^^^

Start a new NCP server.

.. code:: python

    loop = asyncio.get_event_loop()
    server = loop.run_until_complete(_start_server(client_connected))
    try:
        loop.run_forever()
    finally:
        server.close()
        loop.run_until_complete(server.wait_closed())


Advanced usage
^^^^^^^^^^^^^^

-   :doc:`NCP connection documentation <connection>`.


API reference
-------------

.. autofunction:: start_server

.. autoclass:: Server
    :members:
"""


import asyncio
import logging
from ncplib.connection import Connection
from ncplib.errors import NCPError


__all__ = (
    "start_server",
    "Server",
)


logger = logging.getLogger(__name__)


def _server_predicate(field):
    return True


class Server:

    """
    A :doc:`server`.

    Servers can be used as *async context managers* to automatically shut down the server:

    .. code:: python

        async with server:
            pass

        # Server is automatically shut down.

    .. important::

        Do not instantiate this class directly. Use :func:`start_server` to create a :class:`Server`.
    """

    def __init__(self, client_connected, host, port, *, auto_link, auto_auth):
        self._client_connected = client_connected
        self._host = host
        self._port = port
        # Config.
        self._auto_link = auto_link
        self._auto_auth = auto_auth
        # Handlers.
        self._handlers = set()

    async def _run_client_connected(self, reader, writer):
        connection = Connection(
            reader, writer, _server_predicate,
            logger=logger,
            remote_hostname=":".join(map(str, writer.get_extra_info("peername")[:2])),
            auto_link=self._auto_link,
            send_errors=True,
        )
        try:
            # Handle auto-auth.
            if self._auto_auth:
                connection.send("LINK", "HELO")
                # Read the hostname.
                field = await connection.recv_field("LINK", "CCRE")
                try:
                    connection.remote_hostname = str(field["CIW"])
                except KeyError:
                    # Handle authentication failure.
                    logger.warning("Invalid authentication from %s over NCP", connection.remote_hostname)
                    field.send(ERRO="CIW - This field is required", ERRC=401)
                    return
                # Complete authentication.
                connection.send("LINK", "SCAR")
                await connection.recv_field("LINK", "CARE")
                connection.send("LINK", "SCON")
            # Handle connection.
            connection._start_tasks()
            await self._client_connected(connection)
        # Close the connection.
        except asyncio.CancelledError:  # pragma: no cover
            raise  # Propagate cancels.
        except NCPError as ex:  # Warnings on client decode error.
            logger.warning("Connection error from %s over NCP: %s", connection.remote_hostname, ex)
            if not connection.is_closing():
                connection.send("LINK", "ERRO", ERRO="Bad request", ERRC=400)
        except Exception as ex:
            logger.exception("Unexpected error from %s over NCP", connection.remote_hostname, exc_info=ex)
            if not connection.is_closing():
                connection.send("LINK", "ERRO", ERRO="Server error", ERRC=500)
        finally:
            connection.close()
            await connection.wait_closed()

    def _handle_client_connected(self, reader, writer):
        handler = asyncio.get_running_loop().create_task(self._run_client_connected(reader, writer))
        handler.add_done_callback(self._handlers.remove)
        self._handlers.add(handler)

    async def _connect(self):
        self._server = await asyncio.start_server(self._handle_client_connected, self._host, self._port)
        for socket in self.sockets:
            logger.info("Listening on %s:%s over NCP", *socket.getsockname()[:2])

    @property
    def sockets(self):
        """
        A list of the connected listening sockets.
        """
        return self._server.sockets

    def close(self):
        """
        Shuts down the server.

        After calling this method, use :meth:`wait_closed` to wait for the server to fully shut down.

        .. hint::

            If you use the server as an *async context manager*, there's no need to call :meth:`Server.close`
            manually.
        """
        # Close the server.
        self._server.close()
        # Stop handlers.
        for handler in self._handlers:
            handler.cancel()

    async def wait_closed(self):
        """
        Waits for the server to fully shut down.

        This method is a *coroutine*.

        .. important::

            Only call this method after first calling :meth:`close`.

        .. hint::

            If you use the server as an *async context manager*, there's no need to call
            :meth:`Server.wait_closed` manually.
        """
        # Wait for handlers to complete.
        if self._handlers:
            await asyncio.wait(self._handlers)
        # Wait for the server to shut down.
        await self._server.wait_closed()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.close()
        await self.wait_closed()


async def start_server(client_connected, host="0.0.0.0", port=9999, *, auto_link=True, auto_auth=True):
    """
    Creates and returns a new :class:`Server` on the given host and port.

    :param client_connected: A coroutine function taking a single :class:`Connection`
            argument representing the client connection. When the connection handler exits, the :class:`Connection`
            will automatically close. If the client closes the connection, the connection handler will exit.
    :param str host: The host to bind the server to.
    :param int port: The port to bind the server to.
    :param bool auto_link: Automatically send periodic LINK packets over the connection.
    :param bool auto_auth: Automatically perform the :term:`NCP` authentication handshake on client connect.
    :return: The created :class:`Server`.
    :rtype: Server
    """
    server = Server(client_connected, host, port, auto_link=auto_link, auto_auth=auto_auth)
    await server._connect()
    return server
