CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

ALTER TABLE network.network RENAME COLUMN "JTE_ID_BEG" TO source;
ALTER TABLE network.network RENAME COLUMN "JTE_ID_END" TO target;
ALTER TABLE network.network RENAME COLUMN "Snelheden_MAXSHD" TO maxspeed;
ALTER TABLE network.network RENAME COLUMN "BST_CODE" TO road_type;
ALTER TABLE network.network RENAME COLUMN "RIJRICHTNG" TO direction;

UPDATE network.network SET maxspeed = NULL WHERE maxspeed IN ('NVT', 'NOA');
ALTER TABLE network.network ALTER COLUMN maxspeed TYPE integer USING (NULLIF(maxspeed, '')::integer);

ALTER TABLE network.network ADD COLUMN IF NOT EXISTS length double precision;
UPDATE network.network SET length = ST_Length(geom);

ALTER TABLE network.network ADD COLUMN IF NOT EXISTS cost double precision;
ALTER TABLE network.network ADD COLUMN IF NOT EXISTS reverse_cost double precision;

UPDATE network.network
SET 
cost = CASE 
          WHEN road_type IN ('VP')
                THEN ST_Length(geom) / (5/3.6) -- 5 km/u voor voetpad
          WHEN road_type IN ('FP')
                THEN ST_Length(geom) / (15/3.6) -- 15 km/u voor fietspad
          WHEN (road_type NOT IN ('FP', 'VP') OR road_type IS NULL) AND maxspeed IS NOT NULL
                THEN ST_Length(geom) / (maxspeed/3.6)
          WHEN (road_type NOT IN ('FP', 'VP') OR road_type IS NULL) AND maxspeed IS NULL
                THEN ST_Length(geom) / (30/3.6) -- 30 km/u aanname
        END,
reverse_cost = CASE 
          WHEN road_type IN ('VP')
                THEN ST_Length(geom) / (5/3.6) -- 5 km/u voor voetpad
          WHEN road_type IN ('FP')
                THEN ST_Length(geom) / (15/3.6) -- 15 km/u voor fietspad
          WHEN (road_type NOT IN ('FP', 'VP') OR road_type IS NULL) AND maxspeed IS NOT NULL
                THEN ST_Length(geom) / (maxspeed/3.6)
          WHEN (road_type NOT IN ('FP', 'VP') OR road_type IS NULL) AND maxspeed IS NULL
                THEN ST_Length(geom) / (30/3.6) -- 30 km/u aanname
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

CREATE INDEX IF NOT EXISTS idx_network_source ON network.network (source);
CREATE INDEX IF NOT EXISTS idx_network_target ON network.network (target);