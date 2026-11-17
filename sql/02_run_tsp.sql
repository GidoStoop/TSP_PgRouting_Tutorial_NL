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