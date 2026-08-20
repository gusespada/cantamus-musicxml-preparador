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
- Ofrece un flujo bilingüe optativo con vista previa editable y confirmación individual.
- Reconoce indicaciones «swing» o «con swing» y escribe 4/4 como 12/8 y 2/4 como 6/8.
- Si no hay tempo, agrega negra = 100 en compás simple o negra con puntillo = 100 en compuesto.
- Revisa los silencios invisibles que suelen quedar después de Audiveris/MuseScore: elimina sólo los que sobran con certeza y vuelve visibles los que completan el compás.
- Normaliza soprano, alto, tenor y bajo como instrumentos vocales para evitar que Sibelius interprete las voces graves como instrumentos transpositores.
- Comprueba las claves de tenor y bajo. Si una clave de tenor con 8 hace que sus alturas MusicXML queden por debajo del bajo, sube los `<pitch>` del tenor una octava; los casos no demostrables sólo se informan.

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

El reconocimiento de swing y las correcciones seguras de silencios invisibles están activos de manera predeterminada. Para hacer solamente una auditoría sin esas transformaciones se pueden agregar `--no-swing` o `--no-ghost-fixes`. Algunos exportadores muestran «con swing» en la partitura pero omiten ese texto del MusicXML; en esos casos se usa `--force-swing` después de comprobar la indicación en la fuente.

## Partituras bilingües

Cantamus utiliza un solo idioma de voz por partitura. Cuando una obra combina, por ejemplo, español e inglés, el preparador puede proponer una escritura fonética aproximada del inglés para una voz configurada en español. Esta función es optativa: primero genera una vista previa y no cambia ninguna sílaba hasta que cada reemplazo haya sido confirmado.

El archivo de configuración JSON declara:

- `primary_language`: idioma elegido para la voz de Cantamus, por ejemplo `es`.
- `secondary_language`: idioma que necesita transcripción, por ejemplo `en`.
- `replacements`: diccionario por lote de texto original a fonética aproximada.
- `passages`: lista opcional de partes y rangos de compases del idioma secundario. Si se omite, el script sólo propone coincidencias exactas del diccionario.

Hay un modelo editable en [`examples/bilingue-en-es.json`](examples/bilingue-en-es.json). Para crear la vista previa:

```bash
python3 cantamus_optimize.py entrada.musicxml vista-previa.musicxml \
  --bilingual-config examples/bilingue-en-es.json \
  --phonetic-preview fonetica.csv \
  --report vista-previa.md
```

Abrí `fonetica.csv` con Excel, Numbers o una planilla compatible. Cada fila muestra idioma principal y secundario, voz, compás, nota, verso, texto original, propuesta fonética y estado. Corregí la columna `phonetic` cuando sea necesario y escribí `yes` en `confirmed` únicamente para los reemplazos aprobados.

Para aplicar la revisión:

```bash
python3 cantamus_optimize.py entrada.musicxml salida-Cantamus.musicxml \
  --bilingual-config examples/bilingue-en-es.json \
  --apply-phonetics fonetica.csv \
  --report auditoria.md
```

Antes de modificar cada sílaba, el script verifica parte, compás, nota, verso y texto original exacto. Si algo cambió desde la vista previa, se detiene; no intenta adivinar ni altera silenciosamente el idioma principal. Las letras con elisiones nativas o estructuras múltiples se marcan como ambiguas y requieren revisión manual.

## Límites y revisión final

El script despliega repeticiones secuenciales simples y casillas numeradas. Si encuentra navegación mediante D.C., D.S. o Coda, letras incompatibles con las pasadas disponibles o una estructura circular, se detiene para evitar inventar música o texto.

Antes de subir el resultado a Cantamus, conviene abrirlo en MuseScore u otro editor compatible, reproducirlo completo y revisar especialmente las sinalefas, los melismas, las notas sin letra y cualquier advertencia de octava. El preparador nunca cambia automáticamente la altura de tenor o bajo cuando la clave y la tesitura no permiten demostrar la corrección.

## Fuentes de referencia

- [Score preparation guidelines for Cantamus](https://voicemod.notion.site/Score-preparation-guidelines-for-Cantamus-9c73c966a82c443bb4064f498b1a4e37)
- [Cantamus Manual](https://voicemod.notion.site/Cant-mus-Manual-7872a77102834187beedb57314689683)

## Licencia

Este proyecto se distribuye bajo la [licencia MIT](LICENSE).
