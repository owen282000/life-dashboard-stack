"""The read-only login roles grafana_ro and dash_ro, created by the receiver or by someone else.

With Docker Compose the receiver connects as a superuser, so at every start it creates a
role when it is missing and sets its password and its settings (a statement timeout and
read-only sessions). A user with CREATEROLE but no SUPERUSER, as on most managed Postgres
services, does the same for a role it created itself. Where the receiver's user may not
manage a role, such as the app user of a CloudNativePG cluster, the role has to exist
already: the cluster's managed roles create it with its password, and a superuser sets its
settings. The receiver then leaves the role as it is, says so in its log, and only grants
it what it may read, which needs no more than owning the tables and views.

The receiver checks its privileges first and sends no role statement it may not run, so a
refused statement never puts a password in the server's log. A password goes to Postgres
as a SCRAM-SHA-256 verifier, never as plain text.
"""

import sys

import psycopg
from psycopg import sql


class RoleError(Exception):
    """A role the stack needs is missing or can't be set up, and the receiver says why."""


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def missing_settings(conn, role: str, settings: dict[str, str]) -> list[str]:
    """The settings the role lacks, for every database or for this one."""
    rows = conn.execute(
        "SELECT unnest(s.setconfig) FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole"
        " WHERE r.rolname = %s AND s.setdatabase IN"
        " (0, (SELECT oid FROM pg_database WHERE datname = current_database()))",
        (role,),
    ).fetchall()
    present = {row[0].split("=", 1)[0].lower(): row[0].split("=", 1)[1] for row in rows}
    return [name for name, value in settings.items() if present.get(name) != value]


def own_attributes(conn) -> dict[str, bool]:
    row = conn.execute(
        "SELECT current_user, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls"
        " FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    return {"user": row[0], "superuser": row[1], "createrole": row[2], "createdb": row[3],
            "replication": row[4], "bypassrls": row[5]}


def may_manage(conn, me: dict, role: str, exists: bool) -> bool:
    """Whether the current user may create the role, or alter it when it exists.

    A superuser may. A user with CREATEROLE may create a role, and from Postgres 16 on alter
    one only when it holds ADMIN OPTION on it, which it gets for a role it created itself.
    """
    if me["superuser"]:
        return True
    if not me["createrole"]:
        return False
    if not exists or conn.info.server_version < 160000:
        return True
    return conn.execute("SELECT pg_has_role(current_user, %s, 'MEMBER WITH ADMIN OPTION')",
                        (role,)).fetchone()[0]


def role_attributes(me: dict) -> list[str]:
    """LOGIN and the NO... attributes the current user may name.

    A user without SUPERUSER may only name an attribute it holds itself (Postgres 16 on); a
    role it creates lacks the others anyway.
    """
    attrs = ["LOGIN", "NOCREATEROLE"]
    for attr in ("superuser", "createdb", "replication", "bypassrls"):
        if me["superuser"] or me[attr]:
            attrs.append("NO" + attr.upper())
    return attrs


def ensure_login_role(conn, role: str, password: str, settings: dict[str, str], password_env: str) -> bool:
    """Create or update a login role; returns False when it is managed outside the receiver.

    Runs inside the caller's transaction, with each step in a savepoint, so a failure names
    the statement that failed and leaves that transaction usable.
    """
    me = own_attributes(conn)
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is not None
    if not may_manage(conn, me, role, exists):
        if not exists:
            raise RoleError(
                f"the database role {role} does not exist, and {me['user']} may not create it. "
                f"Create {role} as a superuser with LOGIN and a password, or let CloudNativePG create "
                "it with its managed roles (docs/kubernetes.md). The receiver then grants it what it "
                "may read."
            )
        log(f"{role} is managed outside the receiver: {me['user']} may not alter it, so {password_env} "
            f"does not set its password and its settings stay as they are. The receiver still grants "
            f"{role} what it may read.")
        missing = missing_settings(conn, role, settings)
        if missing:
            fix = " ".join(f"ALTER ROLE {role} SET {name} = '{settings[name]}';" for name in missing)
            log(f"WARNING: {role} lacks {', '.join(missing)}. It can still only read what it is granted, "
                f"but a superuser should add them: {fix}")
        return False

    verifier = conn.pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256").decode()
    steps = []
    if not exists:
        steps.append((f"CREATE ROLE {role}", sql.SQL("CREATE ROLE {}").format(sql.Identifier(role))))
    attrs = " ".join(role_attributes(me))
    steps.append((f"ALTER ROLE {role} WITH {attrs} PASSWORD <SCRAM verifier>",
                  sql.SQL("ALTER ROLE {} WITH " + attrs + " PASSWORD {}").format(
                      sql.Identifier(role), sql.Literal(verifier))))
    for name, value in settings.items():
        steps.append((f"ALTER ROLE {role} SET {name} = '{value}'",
                      sql.SQL("ALTER ROLE {} SET {} = {}").format(
                          sql.Identifier(role), sql.Identifier(name), sql.Literal(value))))
    for described, statement in steps:
        try:
            with conn.transaction():
                conn.execute(statement)
        except psycopg.Error as exc:
            raise RoleError(f"setting up the role {role} as {me['user']} failed at {described}: "
                            f"{str(exc).strip()}") from exc
    return True
