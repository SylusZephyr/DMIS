"""Bootstrap the project: create data directories and apply the DuckDB schema.

Safe to re-run: table creation is idempotent (CREATE TABLE IF NOT EXISTS)
and directory creation is idempotent (mkdir with exist_ok).
"""

from dmie.database.connection import PROJECT_ROOT, get_connection

DATA_SUBDIRS = ["raw", "interim", "processed", "validated", "exports", "samples"]
SCHEMA_PATH = PROJECT_ROOT / "src" / "dmie" / "database" / "schema.sql"


def init_data_dirs() -> None:
    for name in DATA_SUBDIRS:
        (PROJECT_ROOT / "data" / name).mkdir(parents=True, exist_ok=True)


def init_schema() -> None:
    con = get_connection()
    try:
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        for statement in sql.split(";"):
            statement = statement.strip()
            if not statement:
                continue
            statement = statement.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)
            con.execute(statement)
    finally:
        con.close()


def main() -> None:
    init_data_dirs()
    init_schema()
    print(f"Project initialized. Database: {PROJECT_ROOT / 'database' / 'dmie.duckdb'}")


if __name__ == "__main__":
    main()
