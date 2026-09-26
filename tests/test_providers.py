"""Provider failures must not manufacture successful generation or expose bodies."""
import asyncio

import httpx
import pytest

from providers import ProviderError, Providers


def run_provider(monkeypatch, response=None, status=200, configured=True):
    for name in ('GENERATOR_BASE_URL', 'GENERATOR_MODEL', 'GENERATOR_API_KEY', 'GENERATOR_REASONING_EFFORT', 'TYPESAFE_API_KEY', 'JEV_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    if configured:
        monkeypatch.setenv('GENERATOR_BASE_URL', 'https://generator.invalid/v1')
        monkeypatch.setenv('GENERATOR_MODEL', 'test-model-exact')
    seen = []
    async def exercise():
        provider = Providers()
        await provider.client.aclose()
        def handle(request):
            seen.append(request)
            return httpx.Response(status, json=response)
        provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            return await provider.generate({'input': 'A fictional test question'}, 'healthy')
        finally:
            await provider.close()
    return exercise, seen


def test_no_configuration_does_not_make_a_request(monkeypatch):
    exercise, seen = run_provider(monkeypatch, configured=False)
    with pytest.raises(ProviderError, match='GENERATOR_BASE_URL'):
        asyncio.run(exercise())
    assert seen == []


@pytest.mark.parametrize('response,match', [
    ({'status': 'completed', 'model': 'wrong-model'}, 'returned model ID'),
    ({'status': 'incomplete', 'model': 'test-model-exact'}, 'did not complete'),
    ({'status': 'completed', 'model': 'test-model-exact', 'output': []}, 'no answer text'),
])
def test_provider_mismatch_and_empty_results_are_rejected(monkeypatch, response, match):
    exercise, seen = run_provider(monkeypatch, response)
    with pytest.raises(ProviderError, match=match):
        asyncio.run(exercise())
    assert len(seen) == 1


def test_remote_error_body_is_not_exposed(monkeypatch):
    exercise, seen = run_provider(monkeypatch, {'message': 'private provider diagnostic'}, status=401)
    with pytest.raises(ProviderError, match='HTTP 401') as error:
        asyncio.run(exercise())
    assert 'private provider diagnostic' not in str(error.value)


def test_generator_preserves_response_evidence_and_omits_unconfigured_options(monkeypatch):
    import json
    response = {'status': 'completed', 'model': 'test-model-exact', 'output': [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'A test answer.'}]}],
        'usage': {'input_tokens': 12, 'output_tokens': 4, 'total_tokens': 16}}
    exercise, seen = run_provider(monkeypatch, response)
    result = asyncio.run(exercise())
    assert result['answer'] == 'A test answer.'
    assert result['generator_response'] == response
    assert result['generator_model'] == 'test-model-exact'
    payload = json.loads(seen[0].content)
    assert payload['store'] is False
    assert 'reasoning' not in payload and 'authorization' not in seen[0].headers
    assert str(seen[0].url) == 'https://generator.invalid/v1/responses'
