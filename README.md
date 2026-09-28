# hourtv-subtitles

Subtítulos en español para el catálogo de HourTV. Una tarea diaria
(`.github/workflows/fetch.yml`) busca cada película y temporada en SubDL por
TMDB id y guarda:

- `movie/<tmdb>.es.srt`
- `tv/<id de la serie en el catálogo>/S01E02.es.srt` (las series del catálogo
  casi no traen tmdb_id; el TMDB real se busca por nombre con el panel)

La app los lee de `raw.githubusercontent.com`. `state.json` recuerda qué ya se
buscó; lo que no se encontró se reintenta a la semana.

Secretos del repositorio: `SUBDL_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`.
