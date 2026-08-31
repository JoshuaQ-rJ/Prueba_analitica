import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from pandas_limpieza import carroceria, combustible, ciudad, marcas, sedes, Pais_origen, ventas_autos

# --- 1. Credenciales desde variables de entorno, no hardcodeadas ---
load_dotenv()
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")

engine = create_engine(f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

# --- 2. Verificacion real de conexion (con una consulta de verdad) ---
try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    print("Conexion exitosa a PostgreSQL")
except SQLAlchemyError as e:
    print(f"Error de conexion: {e}")
    raise SystemExit(1)

# --- 3. Diccionario para no repetir codigo y controlar errores por tabla ---
#     Dimensiones primero, tabla de hechos al final (respeta el orden logico
#     del modelo estrella aunque to_sql no impone llaves foraneas por si solo).
tablas_a_cargar = {
    "dim_paises_origen": Pais_origen,
    "dim_carrocerias": carroceria,
    "dim_combustibles": combustible,
    "dim_ciudades": ciudad,
    "dim_marcas": marcas,
    "dim_sedes": sedes,
    "hechos_ventas_autos": ventas_autos,
}

# --- 4. if_exists="replace" para poder re-correr el script sin duplicar datos ---
for nombre_tabla, df in tablas_a_cargar.items():
    try:
        df.to_sql(
            nombre_tabla,
            con=engine,
            if_exists="replace",   # evita duplicados al re-ejecutar
            index=False,
            method="multi",        # inserts en lote, mas rapido
            chunksize=1000,        # evita saturar memoria con DFs grandes
        )
        print(f"'{nombre_tabla}' cargada: {len(df)} filas")
    except SQLAlchemyError as e:
        print(f"Error cargando '{nombre_tabla}': {e}")

# --- 5. Validacion post-carga: conteo de filas por tabla contra el DataFrame origen ---
print("\n--- Validacion post-carga ---")
with engine.connect() as connection:
    for nombre_tabla, df in tablas_a_cargar.items():
        try:
            conteo = connection.execute(text(f'SELECT COUNT(*) FROM "{nombre_tabla}"')).scalar()
            estado = "OK" if conteo == len(df) else "DIFERENCIA"
            print(f"{nombre_tabla}: esperado={len(df)} en_bd={conteo} [{estado}]")
        except SQLAlchemyError as e:
            print(f"Error validando '{nombre_tabla}': {e}")

engine.dispose()
print("Conexion cerrada")
