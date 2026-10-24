import { describe, expect, it } from 'vitest'
import published from '../../../backend/app/api/v1/openapi.json'
import { baseOf, curlOf, events, exampleOf, fieldsOf, groups, limitsOf, refName, schemas, shared, spans, typeOf, valuesOf } from './contract.ts'
import type { OpenApi, Schema, Words } from './contract.ts'

const ERROR = { description: 'Nothing of this id.', content: { 'application/json': { schema: { $ref: '#/components/schemas/Error' } } } }

const DOCUMENT: OpenApi = {
  openapi: '3.1.0',
  info: { title: 'Emudoc API', version: '1' },
  servers: [{ url: 'https://docs.acme.example/' }],
  tags: [
    { name: 'projects', 'x-displayName': 'Projects', description: 'The projects.' },
    { name: 'unused', description: 'Lists nothing.' },
  ],
  paths: {
    '/api/v1/projects/{project_id}/search': {
      get: {
        tags: ['search'],
        summary: 'Search',
        operationId: 'search',
        parameters: [
          { name: 'project_id', in: 'path', required: true, schema: { type: 'integer' } },
          { name: 'q', in: 'query', required: true, schema: { type: 'string', examples: ["the supplier's terms"] } },
          { name: 'limit', in: 'query', schema: { type: 'integer', minimum: 1, maximum: 50, default: 10 } },
        ],
        responses: { '200': { description: 'Found.', content: { 'application/json': { schema: { $ref: '#/components/schemas/Project' } } } }, '404': ERROR },
        security: [{ APIKey: [] }],
      },
    },
    '/api/v1/projects': {
      post: {
        tags: ['projects'],
        summary: 'Create Project',
        description: 'Make a project, which the\norganisation\'s plan holds to `projects`.',
        operationId: 'create_project',
        parameters: [{ name: 'Idempotency-Key', in: 'header', schema: { anyOf: [{ type: 'string' }, { type: 'null' }] } }],
        requestBody: { required: true, content: { 'application/json': { schema: { $ref: '#/components/schemas/NewProject' } } } },
        responses: { '201': { description: 'Made.', content: { 'application/json': { schema: { $ref: '#/components/schemas/Project' } } } }, '404': ERROR, '422': { description: 'Validation Error' } },
        security: [{ APIKey: [] }],
      },
    },
    '/api/v1/documents/{document_id}/file': {
      get: {
        tags: ['documents'],
        operationId: 'download_file',
        responses: { '200': { description: 'The file.', content: { 'application/octet-stream': {} } }, '404': ERROR },
        security: [{ APIKey: [] }],
      },
    },
  },
  webhooks: {
    'document.ready': {
      post: {
        tags: ['webhooks'],
        summary: 'A document is indexed',
        operationId: 'document_ready',
        parameters: [{ name: 'Emudoc-Signature', in: 'header', required: true, schema: { type: 'string' } }],
        requestBody: { required: true, content: { 'application/json': { schema: { $ref: '#/components/schemas/Project' } } } },
        responses: { '2XX': { description: 'Any 2xx answer is a delivery.' } },
      },
    },
  },
  components: {
    schemas: {
      Error: { type: 'object', properties: { detail: { type: 'string' } }, required: ['detail'] },
      NewProject: {
        type: 'object',
        properties: {
          name: { type: 'string', minLength: 1, maxLength: 200, examples: ['Supplier contracts'] },
          visibility: { type: 'string', enum: ['organization', 'private'], default: 'organization' },
        },
        required: ['name'],
      },
      Project: {
        type: 'object',
        properties: {
          id: { type: 'integer' },
          description: { anyOf: [{ type: 'string' }, { type: 'null' }], description: 'What it holds.' },
          name: { type: 'string' },
        },
        required: ['id', 'name'],
      },
    },
    securitySchemes: { APIKey: { type: 'http', scheme: 'bearer', bearerFormat: 'emk_…' } },
  },
}

/** A type's words as one string, a schema named in angle brackets. */
const said = (words: Words) => words.map((word) => (typeof word === 'string' ? word : `<${word.schema}>`)).join('')

describe('the groups of operations', () => {
  it('follows the contract’s tags, those it does not list after, and leaves out a tag that lists nothing', () => {
    const found = groups(DOCUMENT)
    expect(found.map((group) => [group.name, group.title])).toEqual([
      ['projects', 'Projects'],
      ['search', 'Search'],
      ['documents', 'Documents'],
    ])
    expect(found[0].description).toBe('The projects.')
    expect(found[0].operations[0]).toMatchObject({ id: 'create_project', method: 'POST', path: '/api/v1/projects', security: ['APIKey'] })
    expect(found[2].operations[0].summary).toBe('GET /api/v1/documents/{document_id}/file')
  })

  it('lists once the answers every operation gives alike, and each operation’s own', () => {
    expect(shared(DOCUMENT).map((answer) => answer.status)).toEqual(['404'])
    const [made] = groups(DOCUMENT)[0].operations
    expect(made.answers.map((answer) => [answer.status, answer.media])).toEqual([
      ['201', 'application/json'],
      ['422', null],
    ])
    expect(refName(made.answers[0].schema ?? undefined)).toBe('Project')
  })

  it('reads the webhooks’ events, their headers and the body they post', () => {
    const [ready] = events(DOCUMENT)
    expect(ready).toMatchObject({ id: 'document_ready', path: 'document.ready', summary: 'A document is indexed' })
    expect(ready.parameters.map((header) => header.name)).toEqual(['Emudoc-Signature'])
    expect(ready.answers.map((answer) => answer.status)).toEqual(['2XX'])
    expect(refName(ready.body?.schema)).toBe('Project')
  })
})

describe('a schema in words', () => {
  it.each<[Schema, string]>([
    [{ type: 'integer' }, 'integer'],
    [{ $ref: '#/components/schemas/Document' }, '<Document>'],
    [{ anyOf: [{ type: 'integer' }, { type: 'null' }] }, 'integer or null'],
    [{ type: 'array', items: { $ref: '#/components/schemas/Document' } }, 'array of <Document>'],
    [{ anyOf: [{ type: 'array', items: { type: 'integer' } }, { type: 'null' }] }, 'array of integer or null'],
    [{ type: 'string', format: 'date-time' }, 'string (date-time)'],
    [{ type: 'string', contentMediaType: 'application/octet-stream' }, 'file'],
    [{ type: 'object', additionalProperties: { type: 'string' } }, 'object of string'],
    [{ const: 'document.ready' }, '"document.ready"'],
    [{ type: ['string', 'null'] }, 'string or null'],
    [{}, 'any'],
  ])('words %j as %s', (schema, words) => {
    expect(said(typeOf(schema))).toBe(words)
  })

  it('names the values an enum takes, through null too, and what a value is held to', () => {
    expect(valuesOf({ anyOf: [{ type: 'string', enum: ['private', 'project'] }, { type: 'null' }] })).toEqual(['private', 'project'])
    expect(limitsOf({ type: 'integer', minimum: 1, maximum: 200, default: 50 })).toEqual(['1 to 200', 'default 50'])
    expect(limitsOf({ anyOf: [{ type: 'string', minLength: 1, maxLength: 200 }, { type: 'null' }] })).toEqual(['1 to 200 characters'])
    expect(limitsOf({ type: 'string', minLength: 1 })).toEqual([])
    expect(limitsOf({ type: 'array', maxItems: 200, default: '' })).toEqual(['at most 200 items'])
    expect(limitsOf({ type: 'integer', minimum: 0 })).toEqual(['at least 0'])
  })

  it('lists an object’s fields, the required first', () => {
    const fields = fieldsOf(DOCUMENT, { $ref: '#/components/schemas/Project' })
    expect(fields.map((field) => [field.name, field.required, said(field.type)])).toEqual([
      ['id', true, 'integer'],
      ['name', true, 'string'],
      ['description', false, 'string or null'],
    ])
    expect(fields[2].description).toBe('What it holds.')
  })

  it('shows a text with its lines joined and its code set apart', () => {
    expect(spans('Make a project, which the\norganisation holds to `projects`.')).toEqual([
      { code: false, text: 'Make a project, which the organisation holds to ' },
      { code: true, text: 'projects' },
      { code: false, text: '.' },
    ])
  })

  it('makes an example of a body from its required fields and their examples', () => {
    expect(exampleOf(DOCUMENT, { $ref: '#/components/schemas/NewProject' })).toEqual({ name: 'Supplier contracts' })
    expect(exampleOf(DOCUMENT, { type: 'object', properties: { report: { type: 'boolean', examples: [true] }, ids: { type: 'array' } } })).toEqual({ report: true })
    expect(exampleOf(DOCUMENT, { type: 'object', properties: { name: { type: 'string' } } })).toEqual({ name: '…' })
  })
})

describe('a request to try', () => {
  it('sends the key, the required query and an example body, at the deployment’s address', () => {
    const base = baseOf(DOCUMENT, 'http://localhost:5173')
    expect(base).toBe('https://docs.acme.example')
    const [made] = groups(DOCUMENT)[0].operations
    expect(curlOf(DOCUMENT, made, base)).toBe(
      [
        'curl -X POST "https://docs.acme.example/api/v1/projects"',
        '-H "Authorization: Bearer $EMUDOC_API_KEY"',
        '-H "Idempotency-Key: $(uuidgen)"',
        '-H "Content-Type: application/json"',
        `-d '{"name":"Supplier contracts"}'`,
      ].join(' \\\n  '),
    )
    const [search] = groups(DOCUMENT)[1].operations
    expect(curlOf(DOCUMENT, search, base)).toBe(
      [
        'curl -G "https://docs.acme.example/api/v1/projects/$PROJECT_ID/search"',
        `--data-urlencode 'q=the supplier'\\''s terms'`,
        '-H "Authorization: Bearer $EMUDOC_API_KEY"',
      ].join(' \\\n  '),
    )
    const [file] = groups(DOCUMENT)[2].operations
    expect(curlOf(DOCUMENT, file, base).split(' \\\n  ')[0]).toBe('curl -L -OJ "https://docs.acme.example/api/v1/documents/$DOCUMENT_ID/file"')
    expect(baseOf({ ...DOCUMENT, servers: undefined }, 'http://localhost:5173')).toBe('http://localhost:5173')
  })
})

describe('the published contract', () => {
  const contract = published as unknown as OpenApi
  const named = new Set(schemas(contract).map(([name]) => name))
  const words = (schema: Schema | undefined | null) => (schema ? typeOf(schema) : [])

  it('is drawn whole: each schema it names is one of its own, and each type has words', () => {
    const operations = [...groups(contract).flatMap((group) => group.operations), ...events(contract)]
    expect(operations.length).toBeGreaterThan(30)
    const types: [string, Words][] = [
      ...operations.flatMap((operation) => [
        ...operation.parameters.map((parameter): [string, Words] => [`${operation.id} ${parameter.name}`, words(parameter.schema)]),
        [`${operation.id} body`, words(operation.body?.schema)] as [string, Words],
        ...operation.answers.map((answer): [string, Words] => [`${operation.id} ${answer.status}`, words(answer.schema)]),
      ]),
      ...schemas(contract).flatMap(([name, schema]) => fieldsOf(contract, schema).map((field): [string, Words] => [`${name}.${field.name}`, field.type])),
    ]
    for (const [, type] of types) {
      for (const word of type) if (typeof word !== 'string') expect(named).toContain(word.schema)
    }
    // FastAPI's own refusal of a request it could not read echoes what it was sent, of any type.
    const untyped = types.filter(([, type]) => type.some((word) => typeof word === 'string' && /\bany\b/.test(word)))
    expect(untyped.map(([where]) => where)).toEqual(['ValidationError.input'])
    expect(groups(contract).map((group) => group.title)).toContain('Cross-checks')
  })

  it('gives every operation a request with the key, and the upload its files', () => {
    for (const operation of groups(contract).flatMap((group) => group.operations)) {
      expect(curlOf(contract, operation, 'https://docs.acme.example'), operation.id).toContain('-H "Authorization: Bearer $EMUDOC_API_KEY"')
    }
    const upload = groups(contract)
      .flatMap((group) => group.operations)
      .find((operation) => operation.id === 'upload_documents')
    expect(upload && curlOf(contract, upload, 'https://docs.acme.example')).toContain(`-F 'files=@document.pdf'`)
  })
})
