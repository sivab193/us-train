# train — CLI

A thin terminal client over the `train.us.siv19.dev` schedule API.

## Install

```bash
pipx install ./cli
# or, from a checkout:
cd cli && pipx install .
```

## Configure

Point the CLI at an API instance (defaults to `http://localhost:8080`):

```bash
export TRAIN_API_BASE_URL=https://train.us.siv19.dev
```

## Use

```bash
train stations chicago
train departures CHI --date 2026-12-23 --after 08:00
train between CHI SEA --date 2026-12-23
train trip <trip-id>
train status
```

The CLI never touches the database — it only calls the REST API.
