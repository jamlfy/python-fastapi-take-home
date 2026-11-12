-- Clean coffee health records loaded by scripts/load_data.py.
--
-- The loader normalizes text to Title Case and booleans to true/false before
-- inserting. These constraints are defense in depth: they also protect the
-- table from bad writes that bypass the loader.
create table public.coffee_health (
  id integer primary key,
  age smallint not null check (age between 18 and 100),
  gender text not null check (gender in ('Male', 'Female', 'Other')),
  country text not null check (btrim(country) <> ''),
  coffee_intake numeric(4, 1) not null check (coffee_intake between 0 and 10),
  caffeine_mg numeric(6, 1) not null check (caffeine_mg between 0 and 1000),
  sleep_hours numeric(3, 1) not null check (sleep_hours between 0 and 16),
  sleep_quality text not null check (sleep_quality in ('Poor', 'Fair', 'Good', 'Excellent')),
  bmi numeric(4, 1) not null check (bmi between 10 and 60),
  heart_rate smallint not null check (heart_rate between 30 and 220),
  stress_level text not null check (stress_level in ('Low', 'Medium', 'High')),
  physical_activity_hours numeric(4, 1) not null
    check (physical_activity_hours between 0 and 40),
  health_issues text not null check (health_issues in ('None', 'Mild', 'Moderate', 'Severe')),
  occupation text not null check (occupation in ('Office', 'Healthcare', 'Service', 'Student', 'Other')),
  smoking boolean not null,
  alcohol_consumption boolean not null,
  loaded_at timestamptz not null default now(),
  constraint coffee_health_coffee_caffeine_consistency check (
    (coffee_intake = 0 and caffeine_mg <= 10)
    or (coffee_intake > 0 and caffeine_mg > 0)
  )
);

-- The API filters by these columns. The second key column (id) supports the
-- keyset pagination ORDER BY id after filtering by a category/range.
create index ix_coffee_health_country_id on public.coffee_health (country, id);
create index ix_coffee_health_gender_id on public.coffee_health (gender, id);
create index ix_coffee_health_sleep_quality_id on public.coffee_health (sleep_quality, id);
create index ix_coffee_health_stress_level_id on public.coffee_health (stress_level, id);
create index ix_coffee_health_age_id on public.coffee_health (age, id);
create index ix_coffee_health_coffee_intake_id on public.coffee_health (coffee_intake, id);
create index ix_coffee_health_bmi_id on public.coffee_health (bmi, id);
