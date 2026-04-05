-- count - da li su tabele popunjenje
SELECT 'publication_count' AS check, COUNT(*) FROM publication;
SELECT 'author_count' AS check, COUNT(*) FROM author;
SELECT 'publication_author_count' AS check, COUNT(*) FROM publication_author;

-- null provera
SELECT 'null_titles' AS check, COUNT(*) FROM publication WHERE title IS NULL;
SELECT 'null_oai_identifier' AS check, COUNT(*) FROM publication WHERE oai_identifier IS NULL;
SELECT 'null_dates' AS check, COUNT(*) FROM publication WHERE date IS NULL;

-- provera duplikata
SELECT 'duplicate_oai_identifier' AS check, COUNT(*) 
FROM (
    SELECT oai_identifier
    FROM publication
    GROUP BY oai_identifier
    HAVING COUNT(*) > 1
) sub;

-- total -- distinct
SELECT 'distinct_oai_identifier_mismatch' AS check,
       (SELECT COUNT(*) FROM publication) - 
       (SELECT COUNT(DISTINCT oai_identifier) FROM publication);

-- autor provera
SELECT 'empty_authors' AS check, COUNT(*) 
FROM author 
WHERE full_name IS NULL OR full_name = '';

-- autor duplikati (trebalo bi da bude 0 zbog constrainta)
SELECT 'duplicate_author_names' AS check, COUNT(*)
FROM (
    SELECT full_name
    FROM author
    GROUP BY full_name
    HAVING COUNT(*) > 1
) sub;

-- provera relacija
SELECT 'orphan_publication_refs' AS check, COUNT(*)
FROM publication_author pa
LEFT JOIN publication p ON p.id = pa.publication_id
WHERE p.id IS NULL;

SELECT 'orphan_author_refs' AS check, COUNT(*)
FROM publication_author pa
LEFT JOIN author a ON a.id = pa.author_id
WHERE a.id IS NULL;

-- publikacije bez autora (idealno 0, ali sto manje)
SELECT 'publications_without_authors' AS check, COUNT(*)
FROM publication p
LEFT JOIN publication_author pa ON pa.publication_id = p.id
WHERE pa.publication_id IS NULL;

-- autori bez publikac (idealno 0)
SELECT 'authors_without_publications' AS check, COUNT(*)
FROM author a
LEFT JOIN publication_author pa ON pa.author_id = a.id
WHERE pa.author_id IS NULL;

--provera "realisticnosti" podataka

-- prosecan broj autora po publikaciji
SELECT 'avg_authors_per_publication' AS check,
       ROUND(AVG(author_count), 2)
FROM (
    SELECT COUNT(pa.author_id) AS author_count
    FROM publication p
    JOIN publication_author pa ON pa.publication_id = p.id
    GROUP BY p.id
) sub;

-- maks broj autora po publikaciji
SELECT 'max_authors_per_publication' AS check,
       MAX(author_count)
FROM (
    SELECT COUNT(pa.author_id) AS author_count
    FROM publication p
    JOIN publication_author pa ON pa.publication_id = p.id
    GROUP BY p.id
) sub;

--provera datuma
SELECT 'min_date' AS check, MIN(date) FROM publication;
SELECT 'max_date' AS check, MAX(date) FROM publication;