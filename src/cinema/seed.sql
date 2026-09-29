-- Static demo data: users, movies, halls of 10 x 10 seats.
-- Screenings and already-reserved seats are generated randomly by seed.py.

INSERT INTO users (email) VALUES
    ('demo@example.com'),
    ('viewer1@example.com'),
    ('viewer2@example.com'),
    ('viewer3@example.com'),
    ('viewer4@example.com'),
    ('viewer5@example.com');

-- Facts (year, runtime, genres) and posters from ČSFD; descriptions are our own.
INSERT INTO movies (title, duration_minutes, year, genres, description, poster_url, csfd_url) VALUES
    ('Odyssea', 172, 2026,
     '["Akční", "Dobrodružný", "Drama", "Fantasy", "Historický"]',
     'Po pádu Tróje se král Odysseus vydává domů na Ithaku, kde na něj čeká manželka Pénelopé '
     || 'a syn Télemachos. Rozhněvaní bohové mu ale cestu přes moře změní v roky trvající '
     || 'bloudění plné nestvůr, nástrah a zkoušek. Filmové zpracování Homérova eposu.',
     'https://image.pmgstatic.com/cache/resized/w420/files/images/film/posters/171/263/171263738_rerq21.jpg',
     'https://www.csfd.cz/film/1580037-odyssea/prehled/'),
    ('Hra', 129, 1997,
     '["Drama", "Mysteriózní", "Thriller"]',
     'Bohatý a odtažitý bankéř Nicholas van Orton dostane od bratra k narozeninám zvláštní '
     || 'dárek: účast ve „hře“, o jejíchž pravidlech mu nikdo nic neřekne. Hranice mezi hrou '
     || 'a skutečností se brzy začne nebezpečně rozplývat.',
     'https://image.pmgstatic.com/cache/resized/w420/files/images/film/posters/159/397/159397326_bb9eb7.jpg',
     'https://www.csfd.cz/film/2668-hra/prehled/'),
    ('Pulp Fiction: Historky z podsvětí', 154, 1994,
     '["Krimi", "Drama"]',
     'Dva nájemní zabijáci, boxer, který odmítl prohrát zápas, manželka gangstera a dvojice '
     || 'lupičů v jídelně. Tarantinův kultovní film proplétá jejich příběhy z losangeleského '
     || 'podsvětí v nechronologickém pořadí.',
     'https://image.pmgstatic.com/cache/resized/w420/files/images/film/posters/158/336/158336635_61bc7c.jpg',
     'https://www.csfd.cz/film/8852-pulp-fiction-historky-z-podsveti/prehled/');

INSERT INTO halls (name) VALUES ('Hall 1'), ('Hall 2'), ('Hall 3');

WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 10)
INSERT INTO seats (hall_id, row_label, seat_number)
SELECT h.id, char(64 + r.x), s.x
FROM halls h, n AS r, n AS s;

-- v0.2 demo: the last row of every hall is a VIP row whose reservations need approval.
UPDATE seats SET requires_approval = 1 WHERE row_label = 'J';
