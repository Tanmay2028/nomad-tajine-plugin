import logging

import requests

logger = logging.getLogger(__name__)

DEFAULT_SCORE_THRESHOLD = 80.0

# Dairy/egg are also animal products, so they're checked first. Raw ingredients
# like "chicken" sit under "animal"/"animal material", not "... food product".
_VEGETARIAN_ANIMAL_LABELS = {'dairy food product', 'egg food product', 'animal egg'}
_ANIMAL_LABELS = {'animal food product', 'animal', 'animal material'}
_PLANT_LABELS = {'plant food product', 'plant material'}

DEFAULT_SUBSTITUTE_LIMIT = 10


def _ontology_service_url(path: str):
    """URL under nomad-ontology-service, or None if the plugin isn't configured."""
    try:
        from nomad.config import config

        entry_point = config.get_plugin_entry_point(
            'nomad_ontology_service:ontology_service'
        )
    except Exception:
        return None

    base = config.services.api_base_path.rstrip('/')
    prefix = entry_point.prefix.strip('/')
    return f'http://localhost:8000{base}/{prefix}/{path}'


def _local_name(foodon_iri: str) -> str:
    return foodon_iri.rsplit('/', 1)[-1].rsplit('#', 1)[-1]


EXACT_MATCH_SCORE = 100.0
_MIN_PLURAL_LENGTH = 4


def _head_noun_forms(name: str) -> set[str]:
    words = name.strip().lower().split()
    if not words:
        return set()
    last = words[-1]
    if last.endswith('s') and len(last) >= _MIN_PLURAL_LENGTH:
        return {last, last[:-1]}
    return {last}


def _is_unambiguous(ingredient_name: str, results: list[dict]) -> bool:
    top = results[0]
    score = top.get('score', 0)
    return (
        score == EXACT_MATCH_SCORE
        or top['label'].strip().lower() in _head_noun_forms(ingredient_name)
        or len(results) == 1
        or score > results[1].get('score', 0)
    )


def get_foodon_match(
    ingredient_name: str, score_threshold: float = DEFAULT_SCORE_THRESHOLD
):
    """
    Best FoodOn match for an ingredient name, or None if the match is ambiguous.
    "Salt" ties with every label containing "salt" (salt pork, salted butter,
    ...), so guessing would give wrong diet types and substitutes.
    """
    url = _ontology_service_url('food/search')
    if url is None:
        return None

    try:
        response = requests.get(
            url, params={'q': ingredient_name, 'limit': 5}, timeout=30
        )
        response.raise_for_status()
        results = response.json().get('results', [])
    except requests.exceptions.RequestException as e:
        logger.warning(f"FoodOn lookup failed for '{ingredient_name}': {e}")
        return None

    if not results or results[0].get('score', 0) < score_threshold:
        return None
    return results[0] if _is_unambiguous(ingredient_name, results) else None


def get_diet_type_from_superclasses(foodon_iri: str):
    """Diet type from the FoodOn class's ancestors, or None if they don't tell."""
    url = _ontology_service_url(f'food/superclasses/{_local_name(foodon_iri)}')
    if url is None:
        return None

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        superclasses = set(response.json().get('superclasses', []))
    except requests.exceptions.RequestException as e:
        logger.warning(f"FoodOn superclass lookup failed for '{foodon_iri}': {e}")
        return None

    if superclasses & _VEGETARIAN_ANIMAL_LABELS:
        return 'vegetarian'
    if superclasses & _ANIMAL_LABELS:
        return 'omnivorous'
    if superclasses & _PLANT_LABELS:
        return 'vegan'
    return None


def get_foodon_substitutes(
    foodon_iri: str, foodon_label: str, limit: int = DEFAULT_SUBSTITUTE_LIMIT
):
    """Other classes under the same FoodOn parent, e.g. other spices for a spice."""
    class_url = _ontology_service_url(f'food/class/{_local_name(foodon_iri)}')
    if class_url is None:
        return []

    try:
        response = requests.get(class_url, timeout=30)
        response.raise_for_status()
        parents = response.json().get('parents', [])
    except requests.exceptions.RequestException as e:
        logger.warning(f"FoodOn class lookup failed for '{foodon_iri}': {e}")
        return []

    if not parents:
        return []

    # Classes can have several parents; the first is enough for suggestions.
    parent_name = parents[0]['name']
    descendants_url = _ontology_service_url(f'food/descendants/{parent_name}')

    try:
        response = requests.get(descendants_url, timeout=30)
        response.raise_for_status()
        descendants = response.json().get('descendants', [])
    except requests.exceptions.RequestException as e:
        logger.warning(f"FoodOn descendants lookup failed for '{parent_name}': {e}")
        return []

    return [d for d in descendants if d != foodon_label][:limit]
