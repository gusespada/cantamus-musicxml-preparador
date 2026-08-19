# Preparador de MusicXML para Cantamus

Este script prepara partituras MusicXML para Cantamus: despliega repeticiones y casillas, separa las estrofas que aparecen simultáneamente y genera un informe con posibles problemas de letra, sinalefas, divisi, tempo e idioma. Todo el procesamiento ocurre en la computadora del usuario; la partitura no se envía a ningún servidor.

> Proyecto independiente y no oficial. Cantamus y MusicXML pertenecen a sus respectivos titulares.

## Alternativa sin instalación

Quien no quiera utilizar Python ni la línea de comandos puede usar la [aplicación web Cantamus XML](https://cantamus-xml-preparador.gusespada.chatgpt.site/). Funciona directamente en el navegador y procesa el archivo localmente.

## Qué hace

- Despliega repeticiones simples y casillas de primera y segunda vez.
- Coloca una única estrofa en cada pasada musical.
- Elimina letras duplicadas producidas por algunos exportadores.
- Conserva los guiones bajos aceptados como elisiones por Cantamus.
- Detecta separadores de sinalefa mal formados.
- Compara el silabeo alineado entre las voces y señala diferencias para revisar.
- Informa nombres de partes, tempo, idioma, divisi, grupos irregulares y notas que podrían sonar con «ah».
- No modifica automáticamente una letra cuando el resultado es ambiguo.

Una sinalefa une en una sola emisión vocal la vocal final de una palabra y la vocal inicial de la siguiente. En este flujo, el guion bajo dentro de la letra —por ejemplo, `y_al`— representa explícitamente esa unión para Cantamus.

## Requisitos

- Python 3.9 o posterior.
- Un archivo MusicXML sin comprimir, con extensión `.musicxml` o `.xml`.
- No requiere instalar bibliotecas adicionales.

## Instalación

1. Entrá en [Releases](https://github.com/gusespada/cantamus-musicxml-preparador/releases/latest).
2. Descargá `cantamus-musicxml-preparador.zip`.
3. Descomprimí el archivo en una carpeta de tu computadora.

También se puede descargar solamente `cantamus_optimize.py` desde este repositorio.

## Uso

En macOS o Linux:

```bash
python3 cantamus_optimize.py entrada.musicxml salida-Cantamus.musicxml --report auditoria.md
```

En Windows:

```powershell
py cantamus_optimize.py entrada.musicxml salida-Cantamus.musicxml --report auditoria.md
```

El script crea dos archivos:

- `salida-Cantamus.musicxml`: la partitura preparada.
- `auditoria.md`: el informe de comprobación, que puede abrirse con cualquier editor de texto.

Si se omite `--report`, el informe se guarda junto a la salida con el sufijo `-report.md`.

## Límites y revisión final

El script despliega repeticiones secuenciales simples y casillas numeradas. Si encuentra navegación mediante D.C., D.S. o Coda, letras incompatibles con las pasadas disponibles o una estructura circular, se detiene para evitar inventar música o texto.

Antes de subir el resultado a Cantamus, conviene abrirlo en MuseScore u otro editor compatible, reproducirlo completo y revisar especialmente las sinalefas, los melismas y las notas sin letra.

## Fuentes de referencia

- [Score preparation guidelines for Cantamus](https://voicemod.notion.site/Score-preparation-guidelines-for-Cantamus-9c73c966a82c443bb4064f498b1a4e37)
- [Cantamus Manual](https://voicemod.notion.site/Cant-mus-Manual-7872a77102834187beedb57314689683)

## Licencia

Este proyecto se distribuye bajo la [licencia MIT](LICENSE).

