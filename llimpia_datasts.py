"""Herramienta interactiva para limpiar archivos CSV o Excel con pandas."""

from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd


def solicitar_ruta() -> Path | None:
    """Pide una ruta existente de CSV, XLSX o XLS."""
    try:
        ruta = Path('1_finanzas.csv') 
        if not ruta.is_file():
            print("Error: no se encontró el archivo indicado.")
            return None
        if ruta.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
            print("Error: el archivo debe ser CSV, XLSX o XLS.")
            return None
        return ruta
    except (OSError, ValueError) as error:
        print(f"Error al leer la ruta: {error}")
        return None


def detectar_csv(ruta: Path) -> tuple[str, str]:
    """Detecta codificación y separador sin dependencias adicionales."""
    contenido = ruta.read_bytes()[:8192]
    for codificacion in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            muestra = contenido.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    else:  # Es poco probable porque latin-1 admite cualquier byte.
        raise UnicodeError("No fue posible detectar la codificación.")

    try:
        separador = csv.Sniffer().sniff(muestra, delimiters=",;|\t").delimiter
    except csv.Error:
        separador = ","
    return codificacion, separador


def cargar_dataset(ruta: Path) -> tuple[pd.DataFrame, dict] | None:
    """Carga el archivo y conserva metadatos útiles para la exportación."""
    try:
        extension = ruta.suffix.lower()
        if extension == ".csv":
            codificacion, separador = detectar_csv(ruta)
            datos = pd.read_csv(ruta, encoding=codificacion, sep=separador)
            return datos, {"codificacion": codificacion, "separador": separador}

        # Pandas mostrará un mensaje claro si falta el lector opcional de Excel.
        datos = pd.read_excel(ruta)
        return datos, {"codificacion": "utf-8-sig", "separador": ","}
    except FileNotFoundError:
        print("Error: el archivo ya no existe o fue movido.")
    except ImportError:
        print("Error: este entorno no tiene el motor de Excel necesario para abrir ese archivo.")
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError) as error:
        print(f"Error al cargar el dataset: {error}")
    return None


def mostrar_resumen(datos: pd.DataFrame) -> None:
    """Muestra tamaño, tipos, nulos y duplicados del estado actual."""
    try:
        print(f"\nFilas: {len(datos)} | Columnas: {len(datos.columns)}")
        resumen = pd.DataFrame(
            {"tipo": datos.dtypes.astype(str), "nulos": datos.isna().sum()}
        )
        print("\nResumen por columna:")
        print(resumen.to_string())
        print(f"\nFilas duplicadas completas: {int(datos.duplicated().sum())}")
    except (TypeError, ValueError) as error:
        print(f"Error al generar el resumen: {error}")


def pedir_columnas(datos: pd.DataFrame, mensaje: str, predeterminadas: list[str] | None = None) -> list[str]:
    """Obtiene una lista válida de columnas; Enter usa las predeterminadas."""
    print("Columnas disponibles:", ", ".join(map(str, datos.columns)))
    entrada = input(mensaje).strip()
    if not entrada:
        columnas = predeterminadas if predeterminadas is not None else list(datos.columns)
    else:
        columnas = [nombre.strip() for nombre in entrada.split(",") if nombre.strip()]

    inexistentes = [columna for columna in columnas if columna not in datos.columns]
    if inexistentes:
        raise KeyError("Columnas inexistentes: " + ", ".join(inexistentes))
    if not columnas:
        raise ValueError("Debe indicar al menos una columna.")
    return columnas


def eliminar_duplicados(datos: pd.DataFrame) -> pd.DataFrame:
    """Elimina filas repetidas considerando todas sus columnas."""
    try:
        antes = len(datos)
        resultado = datos.drop_duplicates().copy()
        print(f"Se eliminaron {antes - len(resultado)} filas duplicadas.")
        return resultado
    except (TypeError, ValueError) as error:
        print(f"Error al eliminar duplicados: {error}")
        return datos


def valor_fijo_para_columna(valor: str, serie: pd.Series):
    """Convierte un valor fijo al tipo de la columna cuando es posible."""
    if pd.api.types.is_numeric_dtype(serie):
        return float(valor)
    if pd.api.types.is_bool_dtype(serie):
        valores = {"si": True, "sí": True, "true": True, "1": True,
                   "no": False, "false": False, "0": False}
        clave = valor.lower()
        if clave not in valores:
            raise ValueError("Para una columna booleana use sí/no o true/false.")
        return valores[clave]
    if pd.api.types.is_datetime64_any_dtype(serie):
        fecha = pd.to_datetime(valor, errors="raise")
        return fecha
    return valor


def manejar_nulos(datos: pd.DataFrame) -> pd.DataFrame:
    """Elimina o completa valores nulos según la elección del usuario."""
    try:
        print("\n1. Eliminar filas con nulos")
        print("2. Rellenar: media en numéricas y moda en texto")
        print("3. Rellenar con un valor fijo")
        opcion = input("Elige una opción: ").strip()
        if opcion not in {"1", "2", "3"}:
            raise ValueError("Opción de nulos inválida.")

        columnas = pedir_columnas(
            datos, "Columnas separadas por coma (Enter = todas): "
        )
        antes = int(datos[columnas].isna().sum().sum())
        if antes == 0:
            print("Las columnas seleccionadas no contienen nulos.")
            return datos

        resultado = datos.copy()
        if opcion == "1":
            resultado = resultado.dropna(subset=columnas).copy()
            print(f"Se eliminaron {len(datos) - len(resultado)} filas con nulos.")
        elif opcion == "2":
            sin_relleno = []
            convertidas_a_numero = []
            for columna in columnas:
                serie = resultado[columna]
                if not serie.isna().any():
                    continue
                if pd.api.types.is_numeric_dtype(serie) and not pd.api.types.is_bool_dtype(serie):
                    resultado[columna] = serie.fillna(serie.mean())
                else:
                    es_fecha = any(palabra in str(columna).lower() for palabra in ("fecha", "date", "nacimiento"))
                    convertida = convertir_numerico(serie) if not es_fecha else pd.Series(np.nan, index=serie.index)
                    total_no_nulo = int(serie.notna().sum())
                    if total_no_nulo and int(convertida.notna().sum()) / total_no_nulo >= 0.7:
                        resultado[columna] = convertida.fillna(convertida.mean())
                        convertidas_a_numero.append(columna)
                    else:
                        moda = serie.mode(dropna=True)
                        if moda.empty:
                            sin_relleno.append(columna)
                        else:
                            resultado[columna] = serie.fillna(moda.iloc[0])
            print(f"Se completaron {antes - int(resultado[columnas].isna().sum().sum())} valores.")
            if convertidas_a_numero:
                print("Convertidas a número y completadas con media:", ", ".join(convertidas_a_numero))
            if sin_relleno:
                print("Sin cambios en columnas completamente vacías:", ", ".join(sin_relleno))
        else:
            valor = input("Valor fijo para rellenar: ")
            for columna in columnas:
                resultado[columna] = resultado[columna].fillna(
                    valor_fijo_para_columna(valor, resultado[columna])
                )
            print(f"Se completaron {antes - int(resultado[columnas].isna().sum().sum())} valores.")
        return resultado
    except (KeyError, TypeError, ValueError) as error:
        print(f"Error al manejar nulos: {error}")
        return datos


def limpiar_texto(valor: object, estilo: str) -> object:
    """Limpia espacios, controles y unicode inconsistente sin eliminar tildes."""
    if pd.isna(valor) or not isinstance(valor, str):
        return valor
    texto = unicodedata.normalize("NFKC", valor)
    texto = "".join(caracter for caracter in texto if unicodedata.category(caracter)[0] != "C")
    texto = re.sub(r"\s+", " ", texto).strip()
    if estilo == "1":
        return texto.lower()
    if estilo == "2":
        return texto.upper()
    if estilo == "3":
        return texto.title()
    return texto


def normalizar_texto(datos: pd.DataFrame) -> pd.DataFrame:
    """Normaliza columnas de texto seleccionadas."""
    try:
        columnas_texto = [
            columna for columna in datos.columns
            if pd.api.types.is_string_dtype(datos[columna]) or datos[columna].dtype == object
        ]
        if not columnas_texto:
            raise ValueError("No hay columnas de texto para normalizar.")
        columnas = pedir_columnas(
            datos,
            "Columnas de texto (Enter = todas las detectadas): ",
            columnas_texto,
        )
        no_texto = [columna for columna in columnas if columna not in columnas_texto]
        if no_texto:
            raise TypeError("No son columnas de texto: " + ", ".join(no_texto))

        print("1. minúsculas | 2. MAYÚSCULAS | 3. Tipo Título | 4. Conservar mayúsculas")
        estilo = input("Formato: ").strip()
        if estilo not in {"1", "2", "3", "4"}:
            raise ValueError("Formato de texto inválido.")
        resultado = datos.copy()
        for columna in columnas:
            resultado[columna] = resultado[columna].map(lambda valor: limpiar_texto(valor, estilo))
        print("Texto normalizado en:", ", ".join(columnas))
        return resultado
    except (KeyError, TypeError, ValueError) as error:
        print(f"Error al normalizar texto: {error}")
        return datos


def normalizar_fechas(datos: pd.DataFrame) -> pd.DataFrame:
    """Convierte fechas detectadas al formato ISO AAAA-MM-DD."""
    try:
        candidatas = [
            columna for columna in datos.columns
            if pd.api.types.is_datetime64_any_dtype(datos[columna])
            or any(palabra in str(columna).lower() for palabra in ("fecha", "date", "nacimiento"))
        ]
        if not candidatas:
            raise ValueError("No se detectaron columnas de fecha; indíquelas manualmente.")
        columnas = pedir_columnas(
            datos,
            "Columnas de fecha (Enter = detectadas): ",
            candidatas,
        )
        preferir_dia = input("¿Interpretar 05/03/2024 como 5 de marzo? (s/n): ").strip().lower() in {"s", "si", "sí"}
        resultado = datos.copy()

        for columna in columnas:
            original = resultado[columna]
            convertido = pd.to_datetime(
                original, errors="coerce", format="mixed", dayfirst=preferir_dia
            )
            no_vacios = original.notna() & original.astype("string").str.strip().ne("")
            invalidos = int((no_vacios & convertido.isna()).sum())
            if invalidos:
                confirmar = input(
                    f"{columna}: {invalidos} fechas no se pudieron convertir. ¿Continuar? (s/n): "
                ).strip().lower()
                if confirmar not in {"s", "si", "sí"}:
                    print(f"Se omitió la columna {columna}.")
                    continue
            resultado[columna] = convertido.dt.strftime("%Y-%m-%d")
            print(f"{columna}: normalizada; valores inválidos convertidos en nulos: {invalidos}.")
        return resultado
    except (KeyError, TypeError, ValueError) as error:
        print(f"Error al normalizar fechas: {error}")
        return datos


def convertir_numerico(serie: pd.Series) -> pd.Series:
    """Convierte números escritos con moneda, separadores o espacios."""
    def convertir(valor: object) -> float:
        if pd.isna(valor):
            return np.nan
        if isinstance(valor, (int, float, np.number)) and not isinstance(valor, bool):
            return float(valor)
        texto = re.sub(r"[^0-9,.-]", "", str(valor).strip())
        if not texto or texto in {"-", ".", ","}:
            return np.nan
        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif texto.count(","):
            texto = texto.replace(",", "") if len(texto.rsplit(",", 1)[1]) == 3 else texto.replace(",", ".")
        elif texto.count(".") > 1:
            texto = texto.replace(".", "")
        try:
            return float(texto)
        except ValueError:
            return np.nan

    return serie.map(convertir)


def columnas_numericas(datos: pd.DataFrame) -> tuple[list[str], dict[str, pd.Series]]:
    """Incluye columnas numéricas y texto mayoritariamente convertible a número."""
    columnas = []
    conversiones = {}
    for columna in datos.columns:
        serie = datos[columna]
        if pd.api.types.is_bool_dtype(serie):
            continue
        if pd.api.types.is_numeric_dtype(serie):
            columnas.append(columna)
            continue
        # Una fecha escrita como 05/03/2024 no debe tratarse como número.
        if any(palabra in str(columna).lower() for palabra in ("fecha", "date", "nacimiento")):
            continue
        convertida = convertir_numerico(serie)
        valores = int(serie.notna().sum())
        if valores and int(convertida.notna().sum()) / valores >= 0.7:
            columnas.append(columna)
            conversiones[columna] = convertida
    return columnas, conversiones


def manejar_outliers(datos: pd.DataFrame) -> pd.DataFrame:
    """Detecta outliers con IQR y permite eliminarlos o marcarlos."""
    try:
        disponibles, conversiones = columnas_numericas(datos)
        if not disponibles:
            raise TypeError("No hay columnas numéricas ni texto convertible a número.")
        columnas = pedir_columnas(
            datos,
            "Columnas para IQR (Enter = todas las numéricas detectadas): ",
            disponibles,
        )
        incompatibles = [columna for columna in columnas if columna not in disponibles]
        if incompatibles:
            raise TypeError("No son numéricas: " + ", ".join(incompatibles))

        resultado = datos.copy()
        mascara_total = pd.Series(False, index=resultado.index)
        for columna in columnas:
            serie = conversiones.get(columna, resultado[columna])
            if columna in conversiones:
                resultado[columna] = serie
                print(f"{columna}: se convirtió de texto a número para aplicar IQR.")
            q1, q3 = serie.quantile([0.25, 0.75])
            iqr = q3 - q1
            if pd.isna(iqr) or iqr == 0:
                print(f"{columna}: IQR no aplicable (datos insuficientes o sin variación).")
                continue
            limite_inferior, limite_superior = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            mascara = (serie < limite_inferior) | (serie > limite_superior)
            mascara_total |= mascara.fillna(False)
            print(f"{columna}: {int(mascara.sum())} outliers; rango [{limite_inferior:.2f}, {limite_superior:.2f}].")

        total = int(mascara_total.sum())
        if total == 0:
            print("No se detectaron outliers con el método IQR.")
            return resultado
        accion = input(f"Se detectaron {total} filas. 1. Eliminar  2. Solo marcar: ").strip()
        if accion == "1":
            resultado = resultado.loc[~mascara_total].copy()
            print(f"Se eliminaron {total} filas con outliers.")
        elif accion == "2":
            nombre = "es_outlier_iqr"
            contador = 1
            while nombre in resultado.columns:
                contador += 1
                nombre = f"es_outlier_iqr_{contador}"
            resultado[nombre] = mascara_total
            print(f"Outliers marcados en la columna '{nombre}'.")
        else:
            print("Acción inválida: no se realizaron cambios sobre las filas.")
        return resultado
    except (KeyError, TypeError, ValueError) as error:
        print(f"Error al manejar outliers: {error}")
        return datos


def ruta_sin_sobrescribir(ruta: Path) -> Path:
    """Genera una ruta libre para no sobrescribir ningún archivo existente."""
    candidata = ruta
    numero = 1
    while candidata.exists():
        candidata = ruta.with_name(f"{ruta.stem}_{numero}{ruta.suffix}")
        numero += 1
    return candidata


def exportar_dataset(datos: pd.DataFrame, origen: Path, metadatos: dict) -> None:
    """Exporta siempre a un CSV nuevo y preserva el separador del CSV de origen."""
    try:
        propuesta = origen.with_name(f"{origen.stem}_limpio.csv")
        entrada = input(f"Archivo de salida (Enter = {propuesta.name}): ").strip().strip('"')
        destino = Path(entrada) if entrada else propuesta
        if not destino.suffix:
            destino = destino.with_suffix(".csv")
        if destino.suffix.lower() != ".csv":
            raise ValueError("La exportación disponible es CSV; indique un nombre con extensión .csv.")
        if destino.resolve() == origen.resolve():
            raise ValueError("No se permite sobrescribir el archivo original.")
        destino = ruta_sin_sobrescribir(destino)
        datos.to_csv(
            destino,
            index=False,
            encoding=metadatos.get("codificacion", "utf-8-sig"),
            sep=metadatos.get("separador", ","),
        )
        print(f"Dataset limpio exportado en: {destino}")
    except (OSError, ValueError, TypeError) as error:
        print(f"Error al exportar el dataset: {error}")


def mostrar_menu() -> None:
    print("\n" + "=" * 45)
    print("LIMPIEZA INTERACTIVA DE DATASETS")
    print("1. Ver resumen")
    print("2. Eliminar duplicados")
    print("3. Manejar nulos")
    print("4. Normalizar texto")
    print("5. Normalizar fechas")
    print("6. Detectar y manejar outliers (IQR)")
    print("7. Exportar dataset limpio")
    print("8. Salir")


def main() -> None:
    """Controla el menú y garantiza que una opción fallida regrese al inicio."""
    print("Use rutas relativas desde la carpeta actual o rutas completas.")
    ruta = solicitar_ruta()
    if ruta is None:
        return
    cargado = cargar_dataset(ruta)
    if cargado is None:
        return
    datos, metadatos = cargado
    print(f"Dataset cargado correctamente: {len(datos)} filas y {len(datos.columns)} columnas.")

    while True:
        try:
            mostrar_menu()
            opcion = input("Seleccione una opción (1-8): ").strip()
            if opcion == "1":
                mostrar_resumen(datos)
            elif opcion == "2":
                datos = eliminar_duplicados(datos)
            elif opcion == "3":
                datos = manejar_nulos(datos)
            elif opcion == "4":
                datos = normalizar_texto(datos)
            elif opcion == "5":
                datos = normalizar_fechas(datos)
            elif opcion == "6":
                datos = manejar_outliers(datos)
            elif opcion == "7":
                exportar_dataset(datos, ruta, metadatos)
            elif opcion == "8":
                print("Programa finalizado. Los cambios solo se guardan al exportar.")
                break
            else:
                print("Opción inválida. Ingrese un número entre 1 y 8.")
        except (EOFError, KeyboardInterrupt):
            print("\nEntrada cancelada. Regresando al menú.")
        except Exception as error:  # Evita que un caso no previsto cierre la aplicación.
            print(f"Error inesperado: {error}. Regresando al menú.")


if __name__ == "__main__":
    main()
