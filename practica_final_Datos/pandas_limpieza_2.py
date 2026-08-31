"""
ETL - Etapa Extract + Transform
Dataset: Automobile_ventas_practica_SUCIO.csv
Solo se usan pandas y numpy (mismas librerias del script original).
"""

import pandas as pd
import numpy as np

# ============================================================
# EXTRACT
# ============================================================
df = pd.read_csv('Automobile_ventas_practica_SUCIO.csv')

print("--- Diagnostico inicial ---")
print(df.dtypes)
print(df.describe())
duplicados = df.duplicated()
registros_incompletos = df.isnull().sum()
print(f"Registros con datos faltantes:\n{registros_incompletos}")
print(f"Registros duplicados (fila completa): {duplicados.sum()}")
print(f"Registros duplicados por ID_Venta: {df['ID_Venta'].duplicated().sum()}")

# ============================================================
# TRANSFORM
# ============================================================

# 1) Normalizacion de nombres de columnas
df.columns = df.columns.str.strip()

# 2) Eliminacion de duplicados: fila completa y por llave de negocio ID_Venta
df = df.drop_duplicates()
df = df.drop_duplicates(subset=['ID_Venta'], keep='first')

# 3) Limpieza general de columnas de texto: quitar espacios y poner formato titulo
cols_texto = df.select_dtypes(include=['object', 'string']).columns
df[cols_texto] = df[cols_texto].apply(lambda col: col.str.strip().str.title())

# 4) Sede: el texto trae errores de tipeo ademas de mayusculas/espacios
#    (ej. "AutoPlaza Nrote", "CarWorld Pacifco", "VelocityMotor").
#    Se normaliza a una clave sin espacios en mayuscula y se corrige con un diccionario.
mapa_sedes = {
    'AUTOPLAZANORTE': 'Autoplaza Norte',
    'AUTOPLAZANROTE': 'Autoplaza Norte',
    'CARWORLDPACIFICO': 'Carworld Pacifico',
    'CARWORLDPACIFCO': 'Carworld Pacifico',
    'DRIVEHUBCARIBE': 'Drivehub Caribe',
    'DRIVEHUBCARIBEE': 'Drivehub Caribe',
    'MOTORCENTERSUR': 'Motorcenter Sur',
    'PRIMEAUTOANDINO': 'Primeauto Andino',
    'PRIMEAUTOANDIINO': 'Primeauto Andino',
    'VELOCITYMOTORS': 'Velocity Motors',
    'VELOCITYMOTOR': 'Velocity Motors',
}
clave_sede = df['Sede'].str.upper().str.replace(' ', '', regex=False)
df['Sede'] = clave_sede.map(mapa_sedes)

# 5) Manejo de valores nulos (misma logica del script original, moda/promedio)
df['Anio_Venta'] = df['Anio_Venta'].fillna(df['Anio_Venta'].mode()[0])
df['Tipo_Combustible'] = df['Tipo_Combustible'].fillna(df['Tipo_Combustible'].mode()[0])
df['Caballos_Fuerza'] = df['Caballos_Fuerza'].fillna(df['Caballos_Fuerza'].mean())
df['Ciudad_Sede'] = df['Ciudad_Sede'].fillna(df['Ciudad_Sede'].mode()[0])
df['Precio_Construccion'] = df['Precio_Construccion'].fillna(df['Precio_Construccion'].mean())

# 6) Anio_Venta: llega en formatos mixtos ("2025", "2023.0", "01/01/2023").
#    Se extrae solo el año como entero.
anio_texto = df['Anio_Venta'].astype(str).str.strip()
anio_slash = anio_texto.str.extract(r'/(\d{4})$')[0]
anio_simple = anio_texto.str.extract(r'^(\d{4})')[0]
df['Anio_Venta'] = anio_slash.fillna(anio_simple).astype(int)

# 7) Precio_Catalogo_Original y Precio_Publico: traen simbolo "$" y comas de miles
df['Precio_Catalogo_Original'] = df['Precio_Catalogo_Original'].replace('[\$,]', '', regex=True).astype(float)
df['Precio_Publico'] = df['Precio_Publico'].replace('[\$,]', '', regex=True).astype(float)

# 8) Cantidad_Vendida: trae sufijos "u" / "unidades" en mayuscula o minuscula
#    (el script original solo quitaba "U" en mayuscula y fallaba con "138 u").
df['Cantidad_Vendida'] = df['Cantidad_Vendida'].astype(str).str.extract(r'(\d+)').astype(float)

# 9) Columnas derivadas (minimo 2 requeridas por la prueba)
anio_actual = pd.Timestamp.now().year
df['Antiguedad_Vehiculo'] = anio_actual - df['Anio_Venta']
df['Ingreso_Total'] = df['Precio_Publico'] * df['Cantidad_Vendida']
df['Categoria_Precio'] = pd.cut(
    df['Precio_Publico'],
    bins=[0, 10000, 20000, 30000, np.inf],
    labels=['Economico', 'Medio', 'Alto', 'Premium']
).astype(str)

# ============================================================
# VALIDACIONES POST-LIMPIEZA
# ============================================================
registros_incompletos = df.isnull().sum()
print("\n--- Validaciones post-limpieza ---")
print(f"Registros con datos faltantes:\n{registros_incompletos}")
print(f"Duplicados por ID_Venta: {df['ID_Venta'].duplicated().sum()}")
print(f"Rango Anio_Venta: {df['Anio_Venta'].min()} - {df['Anio_Venta'].max()}")
print(f"Rango Precio_Publico: {df['Precio_Publico'].min()} - {df['Precio_Publico'].max()}")
print(f"Cantidad_Vendida negativas o cero: {(df['Cantidad_Vendida'] <= 0).sum()}")
print(df.dtypes)
print(df.head())

df.to_csv('Automobile_ventas_practica_LIMPIO.csv', index=False)

# ============================================================
# MODELO ESTRELLA: dimensiones + tabla de hechos
# ============================================================
df = pd.read_csv('Automobile_ventas_practica_LIMPIO.csv')

Pais_origen = (df[['Pais_Origen']]).drop_duplicates().reset_index(drop=True)
Pais_origen.insert(0, 'ID_Pais_Origen', np.arange(1, len(Pais_origen) + 1))

carroceria = (df[['Tipo_Carroceria']]).drop_duplicates().reset_index(drop=True)
carroceria.insert(0, 'ID_Tipo_Carroceria', np.arange(1, len(carroceria) + 1))

combustible = (df[['Tipo_Combustible']]).drop_duplicates().reset_index(drop=True)
combustible.insert(0, 'ID_Tipo_Combustible', np.arange(1, len(combustible) + 1))

ciudad = (df[['Ciudad_Sede']]).drop_duplicates().reset_index(drop=True)
ciudad.insert(0, 'ID_Ciudad_Sede', np.arange(1, len(ciudad) + 1))

marcas = (df[['Marca', 'Pais_Origen']]).drop_duplicates().reset_index(drop=True)
marcas.insert(0, 'ID_Marca', np.arange(1, len(marcas) + 1))
marcas = marcas.merge(Pais_origen, on='Pais_Origen', how='left')
marcas = marcas.drop(columns=['Pais_Origen'])

sedes = (df[['Sede', 'Ciudad_Sede']]).drop_duplicates().reset_index(drop=True)
sedes.insert(0, 'ID_Sede', np.arange(1, len(sedes) + 1))
sedes = sedes.merge(ciudad, on='Ciudad_Sede', how='left')
sedes = sedes.drop(columns=['Ciudad_Sede'])

ventas_autos = df.copy()
ventas_autos = ventas_autos.merge(Pais_origen, on='Pais_Origen', how='left')
ventas_autos = ventas_autos.merge(carroceria, on='Tipo_Carroceria', how='left')
ventas_autos = ventas_autos.merge(combustible, on='Tipo_Combustible', how='left')
ventas_autos = ventas_autos.merge(marcas[['ID_Marca', 'Marca', 'ID_Pais_Origen']], on=['Marca', 'ID_Pais_Origen'], how='left')
ventas_autos = ventas_autos.merge(sedes.merge(ciudad, on='ID_Ciudad_Sede')[['ID_Sede', 'Sede', 'Ciudad_Sede']], on=['Sede', 'Ciudad_Sede'], how='left')
ventas_autos = ventas_autos.drop(columns=['Pais_Origen', 'Tipo_Carroceria', 'Tipo_Combustible', 'Marca', 'Sede', 'Ciudad_Sede'])

ventas_autos.to_csv('tabla_hechos_ventas.csv', index=False)
df = pd.read_csv('tabla_hechos_ventas.csv')
duplicados = df['ID_Venta'].duplicated()
registros_incompletos = df.isnull().sum()
print("\n--- Validacion tabla de hechos ---")
print(df.dtypes)
print(f"Registros con datos faltantes:\n{registros_incompletos}")
print(f"Registros duplicados: {duplicados.sum()}")
df.drop_duplicates(subset=['ID_Venta'], inplace=True)
print(f"Registros duplicados tras limpieza: {df['ID_Venta'].duplicated().sum()}")

ventas_autos = df.copy()
ventas_autos.to_csv('tabla_hechos_ventas.csv', index=False)

print("\nArchivos generados: Automobile_ventas_practica_LIMPIO.csv, tabla_hechos_ventas.csv")
