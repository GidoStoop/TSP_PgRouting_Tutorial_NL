# pgRouting: Shortest Path en het Traveling Salesperson Problem

Deze tutorial beschrijft hoe je een pgRouting-database opzet, een wegennetwerk voorbereidt, kortste routes berekent en een handelsreizigersprobleem (Traveling Salesperson Problem, TSP) oplost.

Het eerste deel richt zich op het berekenen van de kortste route tussen twee locaties. In het tweede deel breiden we deze werkwijze uit naar een route langs meerdere locaties. Tot slot maken we ook een video van de route die is afgelegd.

Voor deze tutorial hebben we een docker container met pgRouting nodig:

```cmd
docker run -d --name pgRouting-tsp -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=pgRouting-tsp -e POSTGRES_DB=tsp -p 5432:5432 pgrouting/pgrouting:latest
```

Als alternatief is het ook mogelijk om pgRouting te downloaden voor een bestaande PostgreSQL database.

## 1. De PostgreSQL-database opzetten

Open pgAdmin (of vergelijkbare software) en maak een nieuwe verbinding aan met de volgende instellingen:

```text
Naam: pgRouting-tsp
Host: localhost
Port: 5432
Database: tsp
Gebruikersnaam: postgres
Wachtwoord: pgRouting-tsp
```

De database `tsp` is de PostGIS-database die je voor deze tutorial gaat gebruiken.


Activeer de benodigde extensies in de database `tsp`:

```sql
CREATE EXTENSION PostGIS;
CREATE EXTENSION pgRouting;
```

## 2. Het wegennetwerk importeren

Download het GeoPackage **Wegvakken** via [Nationaal Wegenbestand](https://www.nationaalwegenbestand.nl/nwb-downloaden).

Open het bestand Wegvakken in QGIS. Je kunt eventueel alle kolommen verwijderen, behalve `id`, `WVK_ID`, `BST_CODE`, `JTE_ID_BEG`, `JTE_ID_END` en `RIJRICHTNG`. Hierdoor verloopt het importeren sneller.

> **Tip:** Je kunt de maximumsnelheden uit het bestand `Snelheden` koppelen als je routes wilt berekenen op basis van de maximaal toegestane snelheid.

Verbind met de database `tsp` en gebruik de DB Manager van QGIS om de gegevens te importeren. Maak een schema aan met de naam `network` en geef de tabel ook de naam `network`. Vergeet niet om een ruimtelijke index aan te maken.

*Het importeren duurt ongeveer 45 minuten.*

### Alternatief: een databaseback-up terugzetten

Voor de live tutorial lever ik een databaseback-up aan waarin de gegevens van Wegvakken al zijn geïmporteerd.

- Klik met de rechtermuisknop op de database. Kies 'Restore' en selecteer het bestand `network.sql`.

- Als het terugzetten mislukt, kan er sprake zijn van samenvoegingsconflicten. Maak in dat geval een nieuwe, lege database aan en probeer het opnieuw.

## 3. Het wegennetwerk voorbereiden

> **Tip:** Alle stappen uit hoofdstuk 3 en 4 zijn ook samengevoegd in het script `sql/01_prepare_network.sql`.

Na het importeren van de Wegvakken-gegevens hernoemen we een aantal kolommen, zodat ze eenvoudiger te gebruiken zijn:

> **Let op:** Kolomnamen met hoofdletters moeten in SQL tussen dubbele aanhalingstekens staan.

```sql
ALTER TABLE network.network RENAME COLUMN "JTE_ID_BEG" to source;
ALTER TABLE network.network RENAME COLUMN "JTE_ID_END" to target;
ALTER TABLE network.network RENAME COLUMN "Snelheden_MAXSHD" to maxspeed;
ALTER TABLE network.network RENAME COLUMN "BST_CODE" to road_type;
ALTER TABLE network.network RENAME COLUMN "RIJRICHTNG" to direction;
```

Er is een probleem met de kolom `maxspeed`: het datatype is `varchar`. Volgens de documentatie van het NWB bevat deze kolom getallen of de tekstwaarden `NVT` en `NOA`. Deze tekstwaarden kunnen niet naar gehele getallen (`integer`) worden omgezet. Daarom vervangen we ze eerst door `NULL`.

```sql
UPDATE network.network SET maxspeed = NULL WHERE maxspeed IN ('NVT', 'NOA');
ALTER TABLE network.network ALTER COLUMN maxspeed TYPE integer USING (NULLIF(maxspeed, '')::integer);
```

Ons netwerk is bijna klaar. pgRouting gebruikt geen geometrie om te bepalen welke segmenten worden gebruikt, maar een kostenwaarde. We moeten daarom zelf de kosten voor ieder segment berekenen. Een eenvoudige eerste stap is om de lengte van een segment als kostenwaarde te gebruiken. De berekende kortste route is dan ook daadwerkelijk de route met de kleinste afstand.

```sql
ALTER TABLE network.network ADD COLUMN length double precision;
UPDATE network.network SET length = ST_Length(geom);
```

*Omdat het coördinatensysteem EPSG:28992 is, worden de lengtes uitgedrukt in meters.*

### Het netwerk testen

```sql
SELECT * FROM pgr_dijkstra(
    'SELECT id, geom, source, target, length AS cost, length AS reverse_cost
	FROM network.network',
    286313059, --Jordanlaan, Zeist
    157176021, --Krabbenpad, Bergen op Zoom
    directed => false);
```

Volgens Google Maps is deze route 117 km lang, terwijl de waarde van `agg_cost` in ons resultaat slechts 105 km is. Dit verschil is te verklaren: Google Maps berekent de snelste route, terwijl onze huidige kostenfunctie de kortste route op basis van afstand berekent.

## 4. Routekosten berekenen op basis van reistijd

We kunnen ook een andere kostenfunctie gebruiken, namelijk de geschatte tijd die nodig is om een segment af te leggen.

```sql
ALTER TABLE network.network ADD COLUMN cost double precision;
ALTER TABLE network.network ADD COLUMN reverse_cost double precision;
UPDATE network.network
SET 
cost = CASE 
              WHEN road_type IN ('VP')
                    THEN ST_Length(geom) / (5/3.6) -- 5 km/hour assumption for Footpath
              WHEN road_type IN ('FP')
                    THEN ST_Length(geom) / (15/3.6) -- 15 km/hour assumption for Fietspad
              WHEN (road_type NOT IN ('FP', 'VP') OR road_type IS NULL) AND maxspeed IS NOT NULL
                    THEN ST_Length(geom) / (maxspeed/3.6)
              WHEN (road_type NOT IN ('FP', 'VP') OR road_type IS NULL) AND maxspeed IS NULL
                    THEN ST_Length(geom) / (30/3.6) --30 km/h for now
            END,
reverse_cost = CASE 
              WHEN road_type IN ('VP')
                    THEN ST_Length(geom) / (5/3.6) -- 5 km/hour assumption for Footpath
              WHEN road_type IN ('FP')
                    THEN ST_Length(geom) / (15/3.6) -- 15 km/hour assumption for Fietspad
              WHEN (road_type NOT IN ('FP', 'VP') OR road_type IS NULL) AND maxspeed IS NOT NULL
                    THEN ST_Length(geom) / (maxspeed/3.6)
              WHEN (road_type NOT IN ('FP', 'VP') OR road_type IS NULL) AND maxspeed IS NULL
                    THEN ST_Length(geom) / (30/3.6) --30 km/h for now
            END;
            
UPDATE network.network
SET
cost = CASE
        WHEN direction = 'T' THEN -1
        ELSE cost
      END,
reverse_cost = CASE
        WHEN direction = 'H' THEN -1
        ELSE reverse_cost
      END;
```

> **Let op:** Een kostenwaarde van `-1` geeft aan pgRouting aan dat deze edge niet mag worden gebruikt in de berekening.

### De route op basis van reistijd testen

```sql
SELECT * FROM pgr_dijkstra(
    'SELECT id, geom, source, target, cost, reverse_cost
	FROM network.network',
    286313059, --Jordanlaan, Zeist
    157176021, --Krabbenpad, Bergen op Zoom
    directed => false);
```

De geaggregeerde kosten zijn nu 4.267. Dat is een stuk lager dan het resultaat op basis van afstand. Dat komt doordat de kosten niet langer in meters, maar in seconden worden uitgedrukt. Volgens deze berekening duurt het ongeveer 4.267 seconden om de route af te leggen.

### Het resultaat vergelijken met Google Maps

Om onze route met Google Maps te vergelijken, berekenen we de totale afstand van de route die op reistijd is gebaseerd:

```sql
SELECT SUM(length) FROM (
SELECT d.*, n.length FROM pgr_dijkstra(
    'SELECT id, source, target, cost, reverse_cost
     FROM network.network',
    286313059, -- Jordanlaan, Zeist
    157176021, -- Krabbenpad, Bergen op Zoom
    directed => false
) AS d
LEFT JOIN network.network AS n
    ON d.edge = n.id
	);
```

Volgens ons algoritme is de snelste route 113 km. Mogelijk komt dit doordat ons netwerk momenteel alle wegtypen toestaat, waaronder gewone wegen, fietspaden en voetpaden. Daarnaast staat het algoritme toe dat je tegen de rijrichting in rijdt. Laten we de query daarom verder aanscherpen:

```sql
SELECT SUM(length) FROM (
SELECT d.*, n.length FROM pgr_dijkstra(
    'SELECT id, source, target, cost, reverse_cost
     FROM network.network WHERE road_type NOT IN (''FP'', ''VP'') AND road_type IS NOT NULL',
    286313059, -- Jordanlaan, Zeist
    157176021, -- Krabbenpad, Bergen op Zoom
    directed => true
) AS d
LEFT JOIN network.network AS n
    ON d.edge = n.id
	);
```

Het resultaat is 114 km. Daarmee komen we dichter in de buurt van de route die Google Maps voorstelt.

## 5. De route visueel inspecteren in QGIS

Het is niet direct duidelijk waar het resterende verschil vandaan komt. Daarom gaan we de route visueel inspecteren. Hiervoor slaan we het resultaat op in een nieuwe tabel en voegen we de geometrie toe.

```sql
CREATE SCHEMA IF NOT EXISTS results;
CREATE TABLE results.dijkstra AS (
	SELECT d.*, n.length, n.geom FROM pgr_dijkstra(
	    'SELECT id, source, target, cost, reverse_cost
	     FROM network.network WHERE road_type NOT IN (''FP'', ''VP'') AND road_type IS NOT NULL',
	    286313059, -- Jordanlaan, Zeist
	    157176021, -- Krabbenpad, Bergen op Zoom
	    directed => true
	) AS d
	LEFT JOIN network.network AS n
	    ON d.edge = n.id
);
```

We kunnen het resultaat nu in QGIS bekijken. Voeg een basiskaart toe om het probleem te zien: de route loopt dwars door Dordrecht. Als we overal de maximumsnelheid zouden kunnen rijden, zou dit inderdaad de snelste route zijn. Je zou extra regels aan de kostenfunctie kunnen toevoegen om rekening te houden met dit soort situaties, maar dat laten we in deze tutorial buiten beschouwing. Het belangrijkste is dat de werkwijze functioneert.

# Deel 2: Het handelsreizigersprobleem (Traveling Salesperson Problem, TSP)

## 6. De te bezoeken punten verzamelen

Vervolgens gaan we de gegevens opvragen die we willen gebruiken voor het handelsreizigersprobleem. Gebruik hiervoor bij voorkeur puntgegevens.

Voor deze query gebruiken we Overpass Turbo:

<code>
[out:json][timeout:180];

// Find the administrative area of Utrecht
area["name"="Utrecht"]["boundary"="administrative"]["admin_level"="8"]->.utrecht;

// Retrieve all mapped artworks inside the area
nwr["tourism"="artwork"](area.utrecht);

out center tags;
</code>

Exporteer het resultaat naar een geodatabestand, bijvoorbeeld GeoJSON.

Importeer dit bestand ook in de database. Zorg ervoor dat er een ruimtelijke index wordt aangemaakt en dat de gegevens worden getransformeerd naar EPSG:28992 (Amersfoort / RD New). In deze tutorial voeg ik de gegevens toe aan het schema `network`, in een tabel met de naam `tsp_points`.

## 7. De dichtstbijzijnde netwerknodes bepalen

We kunnen nu routes gaan berekenen tussen de verschillende punten in onze datalaag. Er zijn alleen nog twee problemen die we moeten oplossen:

1. In de query voor de route van Zeist naar Bergen op Zoom hebben we de ID's van de nodes gebruikt. We moeten dus weten welke node het dichtst bij ieder datapunt ligt. De nodes zelf hebben in ons netwerk echter nog geen gedefinieerde geometrie.

2. We weten nu hoe we de kortste route tussen twee punten berekenen, maar nog niet hoe we de kortste route met meerdere tussenstops bepalen.

Om het eerste probleem op te lossen, moeten we de dichtstbijzijnde node vinden. Omdat onze database alleen lijngeometrieën bevat en geen nodegeometrieën, zoeken we eerst de dichtstbijzijnde edge. Vervolgens bepalen we welk uiteinde van die edge het dichtst bij het datapunt ligt.

Met de volgende query vinden we de dichtstbijzijnde node voor het eerste punt:

```sql
WITH
start_point AS (
    SELECT geom AS geom
    FROM network.tsp_points
    LIMIT 1
),

nearest_edge_start_point AS (
    SELECT n.*, s.geom AS sgeom
    FROM (SELECT * FROM network.network
	WHERE road_type NOT IN ('FP', 'VP') AND road_type IS NOT NULL) AS n
    CROSS JOIN start_point s
    ORDER BY n.geom <-> s.geom
    LIMIT 1
),

nearest_node_start_point AS (
    SELECT id,
    CASE
        WHEN ST_Distance(ST_StartPoint(nearest_edge_start_point.geom), sgeom)
           < ST_Distance(ST_EndPoint(nearest_edge_start_point.geom), sgeom)
        THEN source
        ELSE target
    END AS nearest_node_start_point
    FROM nearest_edge_start_point
)

SELECT * FROM nearest_node_start_point
```



Nu we weten hoe we de dichtstbijzijnde node kunnen vinden, passen we de query aan zodat voor ieder `tsp_point` de dichtstbijzijnde node aan de tabel wordt toegevoegd:



```sql
ALTER TABLE network.tsp_points ADD COLUMN IF NOT EXISTS nearest_node bigint;
UPDATE network.tsp_points tp
SET nearest_node = nn.nearest_node_id
FROM (
    SELECT
        tp.id,
        CASE
            WHEN ST_Distance(ST_StartPoint(n.geom), tp.geom)
               < ST_Distance(ST_EndPoint(n.geom), tp.geom)
            THEN n.source
            ELSE n.target
        END AS nearest_node_id
    FROM network.tsp_points tp
    CROSS JOIN LATERAL (
        SELECT *
        FROM network.network n
        WHERE road_type NOT IN ('FP', 'VP') AND road_type IS NOT NULL
        ORDER BY n.geom <-> tp.geom
        LIMIT 1
    ) n
) nn
WHERE tp.id = nn.id;
```

## 8. De TSP-volgorde berekenen

Het probleem van de dichtstbijzijnde node is opgelost. Nu moeten we het handelsreizigersprobleem berekenen. Hiervoor gebruiken we de pgRouting-functie `pgr_TSP`. In de documentatie vinden we het volgende voorbeeld:

```sql
SELECT * FROM pgr_TSP(
    $$SELECT * FROM pgr_dijkstraCostMatrix(
        'SELECT id, source, target, cost, reverse_cost FROM edges',
        (SELECT array_agg(id) FROM vertices WHERE id NOT IN (2, 4, 13, 14)),
        directed => false) $$);
```

Laten we dit voorbeeld aanpassen aan onze dataset:

```sql
    SELECT *
    FROM pgr_TSP(
        $$SELECT * FROM pgr_dijkstraCostMatrix(
            'SELECT id, source, target, cost, reverse_cost
             FROM network.network 
             WHERE road_type NOT IN (''FP'', ''VP'') AND road_type IS NOT NULL',
            (SELECT array_agg(DISTINCT nearest_node) FROM network.tsp_points),
            directed => true)$$
    )
```

Het resultaat van deze TSP is een reeks van 445 afzonderlijke Dijkstra-routes. Deze reeks geeft aan in welke volgorde we de nodes moeten bezoeken, maar bevat nog niet de exacte geometrie van de route tussen de nodes. Om de volledige route te verkrijgen, moeten we iedere afzonderlijke Dijkstra-route berekenen en visualiseren.

```sql
WITH tsp AS (
    SELECT *
    FROM pgr_TSP(
        $$SELECT * FROM pgr_dijkstraCostMatrix(
            'SELECT id, source, target, cost, reverse_cost
             FROM network.network 
             WHERE road_type NOT IN (''FP'', ''VP'') AND road_type IS NOT NULL',
            (SELECT array_agg(DISTINCT nearest_node) 
                FROM (SELECT * FROM network.tsp_points LIMIT 10)),
            directed => true)$$
    )
),

-- Get start and end nodes
ordered_pairs AS (
    SELECT seq, node AS start_node,
        LEAD(node) OVER (ORDER BY seq) AS end_node
    FROM tsp
),

routes AS (
    SELECT
        op.seq AS tsp_seq,
        dj.path_seq,
        dj.node,
        dj.edge,
        dj.cost,
        SUM(dj.cost) OVER (ORDER BY op.seq, dj.path_seq) AS agg_cost,
        CASE
            WHEN dj.node = n.source THEN n.geom
            ELSE ST_Reverse(n.geom)
        END AS geom

    FROM ordered_pairs op
    CROSS JOIN LATERAL pgr_dijkstra(
        'SELECT id, source, target, cost, reverse_cost
         FROM network.network
		 WHERE road_type NOT IN (''FP'', ''VP'') AND road_type IS NOT NULL',
        op.start_node,
        op.end_node,
        directed => true
    ) dj
    JOIN network.network n
        ON dj.edge = n.id
    WHERE op.end_node IS NOT NULL
)

SELECT *
FROM routes
ORDER BY tsp_seq, path_seq;
```

> **Let op de prestaties:** In deze query is bewust een limiet ingesteld voor het aantal nodes. Het uitvoeren kan namelijk veel tijd kosten: 10 punten duurden ongeveer 90 seconden. Als de rekentijd lineair toeneemt, zouden alle 445 punten iets meer dan een uur kosten.

## 9. De prestaties verbeteren met een deelnetwerk

Om de rekentijd te verkorten, maken we een tijdelijke tabel waarin onnodige edges zijn uitgesloten. In dit voorbeeld maken we een convex hull rond de te bezoeken punten, met een buffer van 20 km. Kies zelf een bufferafstand die past bij jouw toepassing. Daarnaast filteren we voet- en fietspaden uit het netwerk:

```sql
DROP TABLE IF EXISTS network.network_subset;
CREATE TABLE network.network_subset AS

WITH hull AS (
    SELECT ST_Buffer(ST_ConvexHull(ST_Collect(geom)),
        20000
    ) AS geom
    FROM network.tsp_points
)

SELECT n.*
	FROM network.network n
	CROSS JOIN hull h
	WHERE (n.road_type NOT IN ('FP', 'VP') AND n.road_type IS NOT NULL)
	AND ST_Intersects(n.geom, h.geom);

CREATE SCHEMA IF NOT EXISTS results;
DROP TABLE IF EXISTS results.tsp;
CREATE TABLE results.tsp AS

WITH tsp AS (
    SELECT *
    FROM pgr_TSP(
        $$SELECT * FROM pgr_dijkstraCostMatrix(
            'SELECT id, source, target, cost, reverse_cost
             FROM network.network_subset',
            (SELECT array_agg(DISTINCT nearest_node) FROM network.tsp_points),
            directed => true)$$
    )
),

-- Get start and end nodes
ordered_pairs AS (
    SELECT seq, node AS start_node,
        LEAD(node) OVER (ORDER BY seq) AS end_node
    FROM tsp
),

routes AS (
    SELECT
		row_number() OVER () as fid,
        op.seq AS tsp_seq,
        dj.path_seq,
        dj.node,
        dj.edge,
        dj.cost,
        SUM(dj.cost) OVER (ORDER BY op.seq, dj.path_seq) AS agg_cost,
        CASE
            WHEN dj.node = n.source THEN n.geom
            ELSE ST_Reverse(n.geom)
        END AS geom

    FROM ordered_pairs op
    CROSS JOIN LATERAL pgr_dijkstra(
        'SELECT id, source, target, cost, reverse_cost
         FROM network.network_subset',
        op.start_node,
        op.end_node,
        directed => true
    ) dj
    JOIN network.network n
        ON dj.edge = n.id
    WHERE op.end_node IS NOT NULL
)

SELECT *
FROM routes
ORDER BY tsp_seq, path_seq;

DROP TABLE IF EXISTS network.network_subset;
```

Als het goed is, duurt de query nu ongeveer 3 minuten. Het resultaat wordt opgeslagen in een tabel met de naam `results.tsp`. Exporteer deze tabel vervolgens als GeoPackage met QGIS, Python of een ander geschikt programma. Noem de layer `tsp`.

## 10. De routevideo genereren
Tot slot gaan we een video genereren van de gemaakte route. Hiervoor heb ik met behulp van AI een Python-script gegenereerd. Als je alle stappen hebt gevolgd, dan kan dat met de volgende command:

```cmd 
python make_routevideo.py tsp.gpkg --output mijn_route_v2.mp4 --title "De kortste route langs:" --subtitle "Alle UNESCO-locaties in Nederland" --fps 30 --seconds 15 --agg-cost-mode cumulative --crs EPSG:28992 --measure-crs EPSG:28992
```

Om te voorkomen dat jullie alle Python dependencies moeten installeren, kun je tijdens de live tutorial ook de Geopackage-file naar mij emailen. Ik zal dan de video maken.

## 11. De workflow opnieuw uitvoeren

Volg deze stappen om de workflow opnieuw uit te voeren:

- Vraag je gegevens op via de Overpass API.
- Sla de gegevens in de database op als `tsp_points`, met coördinatensysteem EPSG:28992 en een ruimtelijke index.
- Voer `sql/02_run_tsp.sql` uit.
- Sla het bestand op als GeoPackage. De laagnaam moet `tsp` zijn.
- Voer `make_routevideo.py` uit met de juiste parameters.

## 12. Opmerkingen

- Als je de TSP niet als een gesloten lus wilt berekenen, kun je een dummy node toevoegen. De kosten om vanuit deze node naar de gewenste startnode te gaan, moeten 0 zijn. Ook de kosten om vanuit de gewenste eindnode naar deze dummy node te gaan, moeten 0 zijn. In de omgekeerde richting moeten de kosten juist extreem hoog zijn. Verwijder de dummy node na de berekening.
<br/>
- In deze tutorial gebruik ik het wegennetwerk voor auto's. Als je dit wilt aanpassen voor voetgangers of fietsers, moet je een andere filtering op het netwerk toepassen. Mogelijk moet je ook `reverse_cost` uitschakelen, omdat voetpaden niet eenrichtingsverkeer hebben.
<br/>
- Het algoritme zal in het resultaat veel wegen meerdere keren gebruiken, bijvoorbeeld voor de heen- en terugweg. Dat is in veel gevallen efficiënter. Wil je dit voorkomen, dan kun je de kosten voor het gebruik van een edge verhogen zodra het algoritme deze eenmaal heeft gebruikt. Hoe groter de verhoging, hoe kleiner de kans dat het algoritme hetzelfde segment opnieuw gebruikt.
