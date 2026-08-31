import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from pandas_limpieza import  carroceria, combustible, ciudad, marcas, sedes, Pais_origen, ventas_autos
# --- 1. Credenciales desde variables de entorno, no hardcodeadas ---
load_dotenv()   
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")


engine = create_engine(f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

# --- 2. Verificación real de conexión (con una consulta de verdad) ---
try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    print("Conexión exitosa a PostgreSQL")
except SQLAlchemyError as e:
    print(f"Error de conexión: {e}")
    raise SystemExit(1)

# --- 3. Diccionario para no repetir código y controlar errores por tabla ---
tablas_a_cargar = {
    "Paises_origen": Pais_origen,
    "Carrocerias": carroceria,
    "Combustibles": combustible,
    "Ciudades": ciudad,
    "Marcas": marcas,
    "Sedes": sedes,
    "Ventas_autos": ventas_autos
}

# --- 4. if_exists="replace" si quieres poder re-correr el script sin duplicar,
#         o valida antes con una condición si quieres conservar histórico ---
for nombre_tabla, df in tablas_a_cargar.items():
    try:
        df.to_sql(
            nombre_tabla,
            con=engine,
            if_exists="replace",   # <- evita duplicados al re-ejecutar
            index=False,
            method="multi",        # <- inserts en lote, más rápido
            chunksize=1000,        # <- evita saturar memoria con DFs grandes
        )
        print(f"'{nombre_tabla}' cargada: {len(df)} filas")
    except SQLAlchemyError as e:
        print(f"Error cargando '{nombre_tabla}': {e}")

engine.dispose()
print("Conexión cerrada")