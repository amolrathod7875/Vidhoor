import importlib
import os
import sys
import types

os.environ.setdefault("OTEL_PYTHON_DISABLED", "true")


# ---------------------------------------------------------------------------
# gRPC fallback
# ---------------------------------------------------------------------------

# Only apply gRPC stubs if the real grpc module cannot be imported.
# This preserves grpc-dependent libraries such as qdrant-client when
# grpc is installed and working normally.

_real_grpc_available = False

try:
    real_grpc = importlib.import_module("grpc")
    _real_grpc_available = hasattr(real_grpc, "insecure_channel")
except Exception:
    _real_grpc_available = False


if not _real_grpc_available:
    _grpc_cython = types.ModuleType("grpc._cython")
    _grpc_cython.CompressionAlgorithm = type(
        "CompressionAlgorithm",
        (),
        {
            "none": 0,
            "deflate": 1,
            "gzip": 2,
        },
    )()

    sys.modules["grpc._cython"] = _grpc_cython

    _grpc_typing = types.ModuleType("grpc._typing")
    _grpc_typing.MetadataType = tuple

    sys.modules["grpc._typing"] = _grpc_typing

    _grpc_stub = types.ModuleType("grpc")

    _grpc_stub.ChannelCredentials = lambda *a, **kw: None

    _grpc_stub.Compression = type(
        "Compression",
        (),
        {
            "none": 0,
            "deflate": 1,
            "gzip": 2,
        },
    )()

    _grpc_stub.StatusCode = type(
        "StatusCode",
        (),
        {
            "OK": 0,
            "CANCELLED": 1,
            "UNKNOWN": 2,
        },
    )()

    _grpc_stub.insecure_channel = lambda *a, **kw: None
    _grpc_stub.secure_channel = lambda *a, **kw: None
    _grpc_stub.RpcError = Exception
    _grpc_stub.RpcCredentials = None

    sys.modules["grpc"] = _grpc_stub


# ---------------------------------------------------------------------------
# OpenTelemetry gRPC exporter stub
# ---------------------------------------------------------------------------

_otel_grpc = types.ModuleType(
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
)


class _FakeOTLPSpanExporter:
    def __init__(self, *a, **kw):
        pass


_otel_grpc.OTLPSpanExporter = _FakeOTLPSpanExporter

sys.modules[
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
] = _otel_grpc


# ---------------------------------------------------------------------------
# Oracle fallback
# ---------------------------------------------------------------------------

# IMPORTANT:
#
# The real python-oracledb driver works in thin mode on Windows and Linux
# without requiring Oracle Instant Client.
#
# Previously Vidhoor replaced oracledb with a fake implementation on EVERY
# Windows machine. That caused the running FastAPI application to use
# _FakeConnection even when the real Oracle driver was installed and working.
#
# We now only install the Oracle stub when the REAL oracledb driver genuinely
# cannot be imported.

_real_oracledb_available = False

try:
    real_oracledb = importlib.import_module("oracledb")
    _real_oracledb_available = hasattr(real_oracledb, "connect")
except Exception:
    _real_oracledb_available = False


if not _real_oracledb_available:
    _oracledb_stub = types.ModuleType("oracledb")

    class _FakeCursor:
        def execute(self, *a, **kw):
            pass

        def fetchall(self):
            return []

        def fetchone(self):
            return None

        def fetchmany(self, *a):
            return []

        def close(self):
            pass

        @property
        def description(self):
            return []

        @property
        def rowcount(self):
            return 0

        @property
        def lastrowid(self):
            return None

    class _FakeConnection:
        def cursor(self):
            return _FakeCursor()

        def close(self):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

        def ping(self, *a, **kw):
            pass

    class _FakeDatabaseError(Exception):
        pass

    class _Defaults:
        fetch_lobs = False

    _oracledb_stub.connect = lambda *a, **kw: _FakeConnection()

    _oracledb_stub.Cursor = _FakeCursor
    _oracledb_stub.Connection = _FakeConnection

    _oracledb_stub.DatabaseError = _FakeDatabaseError
    _oracledb_stub.OperationalError = _FakeDatabaseError
    _oracledb_stub.IntegrityError = _FakeDatabaseError
    _oracledb_stub.ProgrammingError = _FakeDatabaseError
    _oracledb_stub.Error = _FakeDatabaseError

    _oracledb_stub.defaults = _Defaults()

    sys.modules["oracledb"] = _oracledb_stub