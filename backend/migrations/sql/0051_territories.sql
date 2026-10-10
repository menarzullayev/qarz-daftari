-- The territory reference and a customer's address (the owner's four decisions of 2026-10-10).
--
-- One reference for the whole platform: region, district, mahalla, street. A shop may then say where a
-- customer lives by picking from it. All of it is behind the platform switch `address_on`; while that
-- is off no statement of the application reads a table below or writes a column added here.
--
-- The reference holds places and nothing else. A district may carry three aggregate counts (people,
-- families, households), which are numbers. It never holds a person: no resident, no household, no
-- name of anybody. A customer is entered by the shop as before, and the address is five columns of the
-- shop's own customer row, under the same forced row-level security as the customer's name and phone.
--
-- Like the shared catalogue (migration 0050) the four tables belong to no shop and are read by every
-- shop, so they are not under the tenant policy. The ordinary application may read them and nothing
-- else; they are written by the import command alone, which connects as the administrators' role. No
-- role may delete a row of them: a place that is no longer in the seed is marked `retired`, is not
-- offered any more, and is still shown on the customers that point at it.

-- ---------------------------------------------------------------------------
-- The reference
-- ---------------------------------------------------------------------------

CREATE TABLE geo_region (
  id          uuid PRIMARY KEY,
  -- The state code of the territory (SOATO): what makes the import safe to run again.
  soato       text NOT NULL UNIQUE CHECK (soato ~ '^[0-9]{2,12}$'),
  name_uz     text NOT NULL CHECK (length(name_uz) BETWEEN 1 AND 120),   -- Uzbek, Latin
  name_ru     text CHECK (length(name_ru) BETWEEN 1 AND 120),
  name_en     text CHECK (length(name_en) BETWEEN 1 AND 120),
  -- The Uzbek name in the matching form of qarz.domain.names: what a search and the order read.
  name_norm   text NOT NULL CHECK (length(name_norm) >= 1),
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE geo_district (
  id          uuid PRIMARY KEY,
  soato       text NOT NULL UNIQUE CHECK (soato ~ '^[0-9]{2,12}$'),
  region_id   uuid NOT NULL REFERENCES geo_region(id),
  name_uz     text NOT NULL CHECK (length(name_uz) BETWEEN 1 AND 120),
  name_ru     text CHECK (length(name_ru) BETWEEN 1 AND 120),
  name_en     text CHECK (length(name_en) BETWEEN 1 AND 120),
  name_norm   text NOT NULL CHECK (length(name_norm) >= 1),
  -- Aggregate counts where they are known. Numbers of a district, never a list of anybody.
  population  bigint CHECK (population > 0),
  families    bigint CHECK (families > 0),
  households  bigint CHECK (households > 0),
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  -- So that a customer's district can be held to the customer's region by a foreign key.
  UNIQUE (id, region_id)
);
CREATE INDEX geo_district_of_region ON geo_district (region_id, name_norm, id) WHERE status = 'active';

-- A mahalla always has a region. Its district is known for a part of the country only: where the seed
-- does not say, `district_id` is null and stays null until a later seed says. `source_group` is the
-- seed's own code that groups the mahallas of one district it does not name; it is kept as a hint for
-- telling two mahallas of the same name apart and links nothing.
CREATE TABLE geo_mahalla (
  id            uuid PRIMARY KEY,
  -- The seed's code of the mahalla when it has one ("c:<code>"), otherwise its region, district and
  -- matching name ("n:<region>:<district or ->:<name>").
  source_key    text NOT NULL UNIQUE CHECK (length(source_key) BETWEEN 3 AND 200),
  code          text CHECK (length(code) BETWEEN 1 AND 24),
  region_id     uuid NOT NULL REFERENCES geo_region(id),
  district_id   uuid,
  source_group  text CHECK (length(source_group) BETWEEN 1 AND 12),
  name_uz       text NOT NULL CHECK (length(name_uz) BETWEEN 1 AND 120),
  name_norm     text NOT NULL CHECK (length(name_norm) >= 1),
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  -- The district, when there is one, is a district of the mahalla's own region.
  FOREIGN KEY (district_id, region_id) REFERENCES geo_district (id, region_id),
  UNIQUE (id, region_id)
);
-- A search is "what was typed is somewhere in the name", within one region or one district.
CREATE INDEX geo_mahalla_search ON geo_mahalla USING gin (name_norm gin_trgm_ops) WHERE status = 'active';
CREATE INDEX geo_mahalla_of_region ON geo_mahalla (region_id, name_norm, id) WHERE status = 'active';
CREATE INDEX geo_mahalla_of_district ON geo_mahalla (district_id, name_norm, id) WHERE status = 'active';

CREATE TABLE geo_street (
  id          uuid PRIMARY KEY,
  mahalla_id  uuid NOT NULL REFERENCES geo_mahalla(id),
  name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  name_norm   text NOT NULL CHECK (length(name_norm) >= 1),
  -- A street of a town or the single "street" a village is.
  kind        text NOT NULL DEFAULT 'street' CHECK (kind IN ('street', 'village')),
  -- Straight, narrow, a dead end or a main road; null when the seed does not say.
  road_type   text CHECK (road_type IN ('straight', 'narrow', 'dead_end', 'main')),
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  -- A street is known by its mahalla and its matching name: the key of the import.
  UNIQUE (mahalla_id, name_norm),
  UNIQUE (id, mahalla_id)
);

-- ---------------------------------------------------------------------------
-- A customer's address
-- ---------------------------------------------------------------------------

-- Everything is optional. An address starts at a region; the street is either a street of the reference
-- or what the shop typed, never both (most of the country has no streets in the reference).
-- `address_at` is when the address was last set: the shop's next customer is offered the same region,
-- district and mahalla, so a village shop does not pick them again for everyone.
ALTER TABLE customer
  ADD COLUMN geo_region_id    uuid REFERENCES geo_region(id),
  ADD COLUMN geo_district_id  uuid,
  ADD COLUMN geo_mahalla_id   uuid,
  ADD COLUMN geo_street_id    uuid,
  ADD COLUMN street_text      text CHECK (length(street_text) BETWEEN 1 AND 120),
  ADD COLUMN address_at       timestamptz,
  -- The chain holds in the database too: the district and the mahalla are of the customer's region,
  -- the street is of the customer's mahalla.
  ADD CONSTRAINT customer_district_of_region
    FOREIGN KEY (geo_district_id, geo_region_id) REFERENCES geo_district (id, region_id),
  ADD CONSTRAINT customer_mahalla_of_region
    FOREIGN KEY (geo_mahalla_id, geo_region_id) REFERENCES geo_mahalla (id, region_id),
  ADD CONSTRAINT customer_street_of_mahalla
    FOREIGN KEY (geo_street_id, geo_mahalla_id) REFERENCES geo_street (id, mahalla_id),
  ADD CONSTRAINT customer_address_starts_at_region
    CHECK (geo_region_id IS NOT NULL
           OR (geo_district_id IS NULL AND geo_mahalla_id IS NULL AND geo_street_id IS NULL
               AND street_text IS NULL)),
  ADD CONSTRAINT customer_street_needs_mahalla CHECK (geo_street_id IS NULL OR geo_mahalla_id IS NOT NULL),
  ADD CONSTRAINT customer_street_one_way CHECK (geo_street_id IS NULL OR street_text IS NULL),
  ADD CONSTRAINT customer_address_at CHECK ((address_at IS NULL) = (geo_region_id IS NULL));
-- The address the shop used last.
CREATE INDEX customer_last_address ON customer (shop_id, address_at DESC) WHERE address_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Rights
-- ---------------------------------------------------------------------------

-- The ordinary application reads the reference and cannot write it. The worker reads it for the
-- owner's export, which says where each customer lives.
GRANT SELECT ON geo_region, geo_district, geo_mahalla, geo_street TO qd_app, qd_worker;
-- The administrators' role loads it (the import command). It is given no DELETE: a row a customer may
-- point at is never removed, only retired.
GRANT SELECT, INSERT, UPDATE ON geo_region, geo_district, geo_mahalla, geo_street TO qd_admin;
