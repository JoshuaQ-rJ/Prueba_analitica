"""Carga un dataset limpio en PostgreSQL con SQL explícito y psycopg2."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values


# -----------------------------------------------------------------------------
# CONFIGURACIÓN MANUAL: edita estos valores antes de ejecutar el script.
# -----------------------------------------------------------------------------
DATASET_PATH = "gitnombre_ejemplo.csv"  # Ruta absoluta o relativa de tu CSV/XLSX limpio. Ej.: r"C:\ruta\finanzas_limpio.csv"

# Según el docker-compose.yml raíz: PostgreSQL se publica en localhost:5433.
DB_HOST = "localhost"       # Host del contenedor; usa localhost si ejecutas Python en tu equipo.
DB_PORT = 5433               # Puerto del host mapeado al 5432 interno del contenedor.
DB_NAME = "practica_db"     # Valor de POSTGRES_DB en docker-compose.yml.
DB_USER = "admin"           # Valor de POSTGRES_USER en docker-compose.yml.
DB_PASSWORD = "password"    # Valor de POSTGRES_PASSWORD en docker-compose.yml.
TABLE_NAME = "ejemplo_finanzas"  # Nombre de la tabla que se creará dentro del esquema público.

# Opcional: escribe el nombre de una columna única para crear/verificar su PK.
# Déjalo en None si el dataset no tiene una llave primaria.
PRIMARY_KEY_COLUMN = None
PAGE_SIZE = 1000  # Número de filas por lote para execute_values.


class EstructuraIncompatibleError(Exception):
    """Indica que una tabla existente no tiene el esquema esperado."""


def detectar_csv(ruta: Path) -> tuple[str, str]:
    """Detecta codificación y separador sin instalar librerías extra."""
    contenido = ruta.read_bytes()[:8192]
    for codificacion in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            muestra = contenido.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnicodeError("No se pudo detectar la codificación del CSV.")

    try:
        separador = csv.Sniffer().sniff(muestra, delimiters=",;|\t").delimiter
    except csv.Error:
        separador = ","
    return codificacion, separador


def leer_dataset(ruta_texto: str) -> pd.DataFrame:
    """Lee CSV o Excel y elimina espacios accidentales de los encabezados."""
    ruta = Path(ruta_texto.strip().strip('"'))
    if not ruta.is_file():
        raise FileNotFoundError(f"No se encontró el dataset: {ruta}")

    extension = ruta.suffix.lower()
    print(f"Leyendo dataset: {ruta}")
    if extension == ".csv":
        codificacion, separador = detectar_csv(ruta)
        datos = pd.read_csv(ruta, encoding=codificacion, sep=separador)
    elif extension in {".xlsx", ".xls"}:
        try:
            datos = pd.read_excel(ruta)
        except ImportError as error:
            raise ImportError(
                "Falta el motor de Excel requerido por pandas para leer este archivo. "
                "Use CSV o instale el motor antes de continuar."
            ) from error
    else:
        raise ValueError("El dataset debe tener extensión .csv, .xlsx o .xls.")

    datos.columns = [str(columna).strip() for columna in datos.columns]
    if not all(datos.columns):
        raise ValueError("Hay encabezados vacíos; asígneles un nombre antes de cargar.")
    repetidas = datos.columns[datos.columns.duplicated()].tolist()
    if repetidas:
        raise ValueError("Hay encabezados repetidos: " + ", ".join(repetidas))
    return convertir_fechas_detectadas(datos)


def convertir_fechas_detectadas(datos: pd.DataFrame) -> pd.DataFrame:
    """Convierte a datetime columnas llamadas fecha/date/time si son fechas válidas."""
    resultado = datos.copy()
    for columna in resultado.columns:
        es_candidata = any(texto in columna.lower() for texto in ("fecha", "date", "time"))
        if not es_candidata or not pd.api.types.is_string_dtype(resultado[columna]):
            continue
        serie = resultado[columna]
        no_nulos = serie.dropna().astype("string").str.strip().ne("")
        cantidad = int(no_nulos.sum())
        if cantidad == 0:
            continue
        convertida = pd.to_datetime(serie, errors="coerce", format="mixed", dayfirst=True)
        exitos = int((convertida.notna() & serie.notna()).sum())
        if exitos / cantidad >= 0.9:
            resultado[columna] = convertida
            print(f"Fecha detectada y convertida: {columna} -> TIMESTAMP")
    return resultado


def tipo_sql(serie: pd.Series) -> str:
    """Traduce los tipos de pandas a tipos explícitos de PostgreSQL."""
    if pd.api.types.is_bool_dtype(serie):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(serie):
        valores = serie.dropna()
        if not valores.empty and (
            valores.min() < np.iinfo(np.int32).min or valores.max() > np.iinfo(np.int32).max
        ):
            return "BIGINT"
        return "INTEGER"
    if pd.api.types.is_float_dtype(serie):
        return "NUMERIC"
    if pd.api.types.is_datetime64tz_dtype(serie):
        return "TIMESTAMP WITH TIME ZONE"
    if pd.api.types.is_datetime64_any_dtype(serie):
        return "TIMESTAMP"
    return "TEXT"


def tipos_dataframe(datos: pd.DataFrame) -> dict[str, str]:
    """Construye el mapa columna -> tipo SQL a partir del DataFrame."""
    return {str(columna): tipo_sql(datos[columna]) for columna in datos.columns}


def tabla_existe(cursor, nombre_tabla: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema() AND table_name = %s
        )
        """,
        (nombre_tabla,),
    )
    return bool(cursor.fetchone()[0])


def columnas_existentes(cursor, nombre_tabla: str) -> dict[str, str]:
    """Obtiene los tipos SQL actuales de una tabla existente."""
    cursor.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = %s
        ORDER BY ordinal_position
        """,
        (nombre_tabla,),
    )
    return dict(cursor.fetchall())


def primary_key_existente(cursor, nombre_tabla: str) -> list[str]:
    """Consulta la clave primaria declarada para una tabla, si existe."""
    cursor.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = current_schema()
          AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        """,
        (nombre_tabla,),
    )
    return [fila[0] for fila in cursor.fetchall()]


def crear_tabla_sql(tipos: dict[str, str], pk: str | None) -> sql.Composed:
    """Genera CREATE TABLE IF NOT EXISTS usando identificadores seguros."""
    definiciones = [
        sql.SQL("{} {}").format(sql.Identifier(columna), sql.SQL(tipo))
        for columna, tipo in tipos.items()
    ]
    if pk:
        definiciones.append(sql.SQL("PRIMARY KEY ({})").format(sql.Identifier(pk)))
    return sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
        sql.Identifier(TABLE_NAME), sql.SQL(", ").join(definiciones)
    )


def tipo_compatible(esperado: str, actual: str) -> bool:
    """Permite equivalencias seguras entre el tipo generado y PostgreSQL."""
    equivalencias = {
        "INTEGER": {"integer"},
        "BIGINT": {"bigint"},
        "NUMERIC": {"numeric"},
        "BOOLEAN": {"boolean"},
        "TIMESTAMP": {"timestamp without time zone"},
        "TIMESTAMP WITH TIME ZONE": {"timestamp with time zone"},
        "TEXT": {"text", "character varying", "character"},
    }
    return actual in equivalencias[esperado]


def validar_estructura(cursor, tipos: dict[str, str], pk_configurada: str | None) -> list[str]:
    """Detiene la carga si una tabla existente no coincide con el dataset."""
    actuales = columnas_existentes(cursor, TABLE_NAME)
    esperadas = set(tipos)
    diferencias = []
    if set(actuales) != esperadas:
        faltantes = esperadas - set(actuales)
        extras = set(actuales) - esperadas
        if faltantes:
            diferencias.append("faltan columnas: " + ", ".join(sorted(faltantes)))
        if extras:
            diferencias.append("sobran columnas: " + ", ".join(sorted(extras)))
    for columna, esperado in tipos.items():
        if columna in actuales and not tipo_compatible(esperado, actuales[columna]):
            diferencias.append(f"{columna}: se esperaba {esperado}, existe {actuales[columna]}")

    pk_actual = primary_key_existente(cursor, TABLE_NAME)
    pk_esperada = [pk_configurada] if pk_configurada else []
    # Si no se configuró una PK, se respeta la que ya tenga la tabla.
    if pk_configurada is not None and pk_actual != pk_esperada:
        diferencias.append(
            f"clave primaria distinta (esperada: {pk_esperada or 'ninguna'}; existente: {pk_actual or 'ninguna'})"
        )
    if diferencias:
        raise EstructuraIncompatibleError("; ".join(diferencias))
    return pk_actual


def validar_primary_key(datos: pd.DataFrame, columna_pk: str | None) -> None:
    """Evita un INSERT fallido por nulos o duplicados dentro del dataset."""
    if not columna_pk:
        return
    if columna_pk not in datos.columns:
        raise ValueError(f"La PK configurada no existe en el dataset: {columna_pk}")
    nulos = int(datos[columna_pk].isna().sum())
    duplicados = int(datos.duplicated(subset=[columna_pk]).sum())
    if nulos or duplicados:
        raise ValueError(
            f"La columna PK '{columna_pk}' tiene {nulos} nulos y {duplicados} valores duplicados. "
            "Limpie el dataset o cambie PRIMARY_KEY_COLUMN."
        )


def adaptar_valor(valor):
    """Convierte nulos y tipos de NumPy/Pandas a valores adaptables por psycopg2."""
    if valor is None or pd.isna(valor):
        return None
    if isinstance(valor, pd.Timestamp):
        return valor.to_pydatetime()
    if isinstance(valor, pd.Timedelta):
        return str(valor)
    if isinstance(valor, np.generic):
        return valor.item()
    return valor


def preparar_registros(datos: pd.DataFrame) -> list[tuple]:
    """Prepara tuplas en el orden de las columnas para execute_values."""
    return [tuple(adaptar_valor(valor) for valor in fila) for fila in datos.itertuples(index=False, name=None)]


def insertar_lote(cursor, conexion, datos: pd.DataFrame) -> None:
    """Inserta las filas mediante execute_values, sin hacer INSERT fila a fila."""
    if datos.empty:
        print("El dataset no contiene filas; se creó/verificó la tabla sin insertar datos.")
        return
    columnas = sql.SQL(", ").join(sql.Identifier(str(columna)) for columna in datos.columns)
    consulta = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(sql.Identifier(TABLE_NAME), columnas)
    registros = preparar_registros(datos)
    print(f"Insertando {len(registros)} filas en lotes de {PAGE_SIZE}...")
    execute_values(cursor, consulta.as_string(conexion), registros, page_size=PAGE_SIZE)


def validar_configuracion() -> None:
    """Comprueba que los valores manuales mínimos estén definidos."""
    if not DATASET_PATH.strip():
        raise ValueError("Configura DATASET_PATH al inicio del archivo antes de ejecutar.")
    if not all([DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, TABLE_NAME]):
        raise ValueError("Completa las variables DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD y TABLE_NAME.")
    if PRIMARY_KEY_COLUMN is not None and not isinstance(PRIMARY_KEY_COLUMN, str):
        raise TypeError("PRIMARY_KEY_COLUMN debe ser texto o None.")


def cargar_a_postgres() -> None:
    """Orquesta lectura, validación, CREATE TABLE e inserción en una transacción."""
    conexion = None
    try:
        validar_configuracion()
        datos = leer_dataset(DATASET_PATH)
        tipos = tipos_dataframe(datos)
        validar_primary_key(datos, PRIMARY_KEY_COLUMN)
        print(f"Dataset listo: {len(datos)} filas, {len(datos.columns)} columnas.")
        print("Tipos detectados:", ", ".join(f"{col}: {tipo}" for col, tipo in tipos.items()))

        print(f"Conectando a PostgreSQL en {DB_HOST}:{DB_PORT}/{DB_NAME}...")
        conexion = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        with conexion.cursor() as cursor:
            ya_existe = tabla_existe(cursor, TABLE_NAME)
            if ya_existe:
                print(f"La tabla '{TABLE_NAME}' ya existe; validando estructura...")
                pk_activa = validar_estructura(cursor, tipos, PRIMARY_KEY_COLUMN)
                validar_primary_key(datos, pk_activa[0] if pk_activa else None)
            else:
                print(f"Creando tabla '{TABLE_NAME}' si no existe...")
                cursor.execute(crear_tabla_sql(tipos, PRIMARY_KEY_COLUMN))

            insertar_lote(cursor, conexion, datos)
        conexion.commit()
        print("Listo: transacción confirmada y conexión cerrada correctamente.")
    except psycopg2.OperationalError as error:
        print("Error de conexión: no se pudo acceder a PostgreSQL.")
        print("Verifica que Docker esté corriendo, el puerto publicado y las credenciales configuradas.")
        print(f"Detalle técnico: {error}")
    except EstructuraIncompatibleError as error:
        if conexion:
            conexion.rollback()
        print(f"Error: la tabla existente tiene una estructura distinta. {error}")
    except psycopg2.errors.UniqueViolation as error:
        if conexion:
            conexion.rollback()
        print("Error: hay filas duplicadas para una PK o una restricción UNIQUE de la tabla.")
        print(f"Detalle técnico: {error.diag.message_detail or error}")
    except (psycopg2.DataError, psycopg2.ProgrammingError) as error:
        if conexion:
            conexion.rollback()
        print(f"Error de SQL o tipo de dato incompatible: {error}")
    except (FileNotFoundError, ImportError, OSError, TypeError, ValueError, pd.errors.ParserError) as error:
        print(f"Error antes de insertar: {error}")
    except psycopg2.Error as error:
        if conexion:
            conexion.rollback()
        print(f"Error de PostgreSQL durante la carga: {error}")
    finally:
        if conexion is not None:
            conexion.close()
            print("Conexión a PostgreSQL cerrada.")


if __name__ == "__main__":
    cargar_a_postgres()
