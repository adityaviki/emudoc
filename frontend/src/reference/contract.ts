// The versioned API's contract as the reference page draws it (reference.html): the OpenAPI 3.1
// document the API serves at /api/v1/openapi.json (backend/app/api/v1/contract.py), read into
// its groups of operations, the webhooks' events and the schemas, each type in words, and a
// request to try with curl. Only what the contract uses is read: FastAPI's schemas, from
// Pydantic's models.

export const CONTRACT = '/api/v1/openapi.json'
// The variable a program keeps its key in, as the upload client reads it (backend/app/client).
export const KEY_VARIABLE = 'EMUDOC_API_KEY'
const SCHEMAS = '#/components/schemas/'

export interface Schema {
  $ref?: string
  type?: string | string[]
  format?: string
  contentMediaType?: string
  enum?: unknown[]
  const?: unknown
  anyOf?: Schema[]
  oneOf?: Schema[]
  allOf?: Schema[]
  items?: Schema
  additionalProperties?: Schema | boolean
  properties?: Record<string, Schema>
  required?: string[]
  title?: string
  description?: string
  default?: unknown
  examples?: unknown[]
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  minItems?: number
  maxItems?: number
}

export interface Parameter {
  name: string
  in: string
  required?: boolean
  description?: string
  schema?: Schema
}

interface Content {
  schema?: Schema
}

interface RawAnswer {
  description?: string
  content?: Record<string, Content>
}

interface RawOperation {
  tags?: string[]
  summary?: string
  description?: string
  operationId?: string
  parameters?: Parameter[]
  requestBody?: { required?: boolean; content?: Record<string, Content> }
  responses?: Record<string, RawAnswer>
  security?: Record<string, string[]>[]
}

export interface SecurityScheme {
  type: string
  scheme?: string
  bearerFormat?: string
  description?: string
}

export interface OpenApi {
  openapi: string
  info: { title: string; version: string; description?: string }
  servers?: { url: string }[]
  tags?: { name: string; description?: string; 'x-displayName'?: string }[]
  paths: Record<string, Record<string, RawOperation>>
  webhooks?: Record<string, Record<string, RawOperation>>
  components?: { schemas?: Record<string, Schema>; securitySchemes?: Record<string, SecurityScheme> }
}

/** What a request sends in its body: one media type and its schema. */
export interface Body {
  media: string
  schema: Schema
  required: boolean
}

/** An answer of one status, with its body's media type and schema when it has one. */
export interface Answer {
  status: string
  description: string
  media: string | null
  schema: Schema | null
}

export interface Operation {
  /** Its anchor on the page: the operation's id, which the contract keeps. */
  id: string
  method: string
  path: string
  summary: string
  description: string
  parameters: Parameter[]
  body: Body | null
  /** Its answers but those every operation shares (`shared`). */
  answers: Answer[]
  /** The names of the security schemes it is called with. */
  security: string[]
}

export interface Group {
  name: string
  title: string
  description: string
  operations: Operation[]
}

/** A piece of a type's words: text, or a schema of the contract, which the page links to. */
export type Words = (string | { schema: string })[]

export interface Field {
  name: string
  type: Words
  required: boolean
  description: string
  values: unknown[]
  limits: string[]
}

const METHODS = ['get', 'put', 'post', 'patch', 'delete', 'head', 'options']
const JSON_MEDIA = 'application/json'

/** The groups of operations in the contract's order of its tags, those of a tag it does not list after. */
export function groups(document: OpenApi): Group[] {
  const operations = operationsOf(document.paths, document)
  const named = (document.tags ?? []).map((tag) => tag.name)
  const used = [...new Set(operations.map((operation) => operation.tag))]
  return [...named.filter((name) => used.includes(name)), ...used.filter((name) => !named.includes(name))].map((name) => {
    const tag = document.tags?.find((found) => found.name === name)
    return {
      name,
      title: tag?.['x-displayName'] ?? titled(name),
      description: tag?.description ?? '',
      operations: operations.filter((operation) => operation.tag === name).map(({ tag: _tag, ...operation }) => operation),
    }
  })
}

/** The events posted to webhooks: each an operation whose body is what is sent and whose parameters are its headers. */
export function events(document: OpenApi): Operation[] {
  return operationsOf(document.webhooks ?? {}, document).map(({ tag: _tag, ...event }) => event)
}

/** The answers every operation of the contract gives alike, which the page lists once. */
export function shared(document: OpenApi): Answer[] {
  const all = Object.values(document.paths).flatMap((operations) =>
    METHODS.filter((method) => operations[method]).map((method) => operations[method].responses ?? {}),
  )
  if (all.length === 0) return []
  return Object.entries(all[0])
    .filter(([status, answer]) => all.every((responses) => responses[status]?.description === answer.description))
    .map(([status, answer]) => answerOf(status, answer))
    .sort((a, b) => a.status.localeCompare(b.status))
}

function operationsOf(paths: Record<string, Record<string, RawOperation>>, document: OpenApi): (Operation & { tag: string })[] {
  const common = new Set(document.paths === paths ? shared(document).map((answer) => answer.status) : [])
  return Object.entries(paths).flatMap(([path, operations]) =>
    METHODS.filter((method) => operations[method]).map((method) => {
      const raw = operations[method]
      const body = Object.entries(raw.requestBody?.content ?? {})[0]
      return {
        id: raw.operationId ?? `${method}-${path}`,
        tag: raw.tags?.[0] ?? 'other',
        method: method.toUpperCase(),
        path,
        summary: raw.summary ?? `${method.toUpperCase()} ${path}`,
        description: raw.description ?? '',
        parameters: raw.parameters ?? [],
        body: body ? { media: body[0], schema: body[1].schema ?? {}, required: raw.requestBody?.required ?? false } : null,
        answers: Object.entries(raw.responses ?? {})
          .filter(([status]) => !common.has(status))
          .map(([status, answer]) => answerOf(status, answer))
          .sort((a, b) => a.status.localeCompare(b.status)),
        security: (raw.security ?? []).flatMap((requirement) => Object.keys(requirement)),
      }
    }),
  )
}

function answerOf(status: string, answer: RawAnswer): Answer {
  const content = Object.entries(answer.content ?? {})[0]
  return { status, description: answer.description ?? '', media: content?.[0] ?? null, schema: content?.[1].schema ?? null }
}

function titled(name: string): string {
  const words = name.replace(/[-_]/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** The name of the contract's schema a `$ref` names, or null for anything else. */
export function refName(schema: Schema | undefined): string | null {
  return schema?.$ref?.startsWith(SCHEMAS) ? schema.$ref.slice(SCHEMAS.length) : null
}

/** The schema itself: the contract's schema a `$ref` names, else the one given. */
export function resolved(document: OpenApi, schema: Schema): Schema {
  const name = refName(schema)
  return (name && document.components?.schemas?.[name]) || schema
}

/** The schemas of the contract, by name, in its order. */
export function schemas(document: OpenApi): [string, Schema][] {
  return Object.entries(document.components?.schemas ?? {})
}

/** The parts of a schema that one of may be, and whether null may be too. */
function parts(schema: Schema): { parts: Schema[]; nullable: boolean } {
  const either = schema.anyOf ?? schema.oneOf
  if (either) {
    const kept = either.filter((part) => part.type !== 'null')
    return { parts: kept, nullable: kept.length < either.length }
  }
  if (Array.isArray(schema.type) && schema.type.includes('null')) {
    const rest = schema.type.filter((type) => type !== 'null')
    return { parts: [{ ...schema, type: rest.length === 1 ? rest[0] : rest }], nullable: true }
  }
  if (schema.allOf?.length === 1) return parts(schema.allOf[0])
  return { parts: [schema], nullable: false }
}

/** A schema's type in words, the schemas it names among them: "array of Document", "integer or null". */
export function typeOf(schema: Schema | undefined): Words {
  if (!schema) return ['any']
  const { parts: kept, nullable } = parts(schema)
  const words: Words = []
  kept.forEach((part, index) => {
    if (index > 0) words.push(' or ')
    words.push(...single(part))
  })
  if (words.length === 0) words.push('any')
  if (nullable) words.push(' or null')
  return joined(words)
}

function single(schema: Schema): Words {
  const name = refName(schema)
  if (name) return [{ schema: name }]
  if (schema.anyOf || schema.oneOf || schema.allOf) return typeOf(schema)
  if (schema.const !== undefined) return [JSON.stringify(schema.const)]
  if (Array.isArray(schema.type)) return [schema.type.join(' or ')]
  if (schema.type === 'array') return ['array of ', ...typeOf(schema.items)]
  if (schema.type === 'string' && schema.contentMediaType) return ['file']
  if (schema.type === 'object' && typeof schema.additionalProperties === 'object')
    return ['object of ', ...typeOf(schema.additionalProperties)]
  if (schema.format) return [`${schema.type ?? 'string'} (${schema.format})`]
  return [schema.type ?? (schema.properties ? 'object' : 'any')]
}

/** Adjacent pieces of text made one, so that the page draws a type as few nodes. */
function joined(words: Words): Words {
  return words.reduce<Words>((all, word) => {
    const last = all[all.length - 1]
    if (typeof word === 'string' && typeof last === 'string') all[all.length - 1] = last + word
    else all.push(word)
    return all
  }, [])
}

/** The values a schema takes, when it names them: an enum's, through a nullable one too. */
export function valuesOf(schema: Schema | undefined): unknown[] {
  if (!schema) return []
  return parts(schema).parts.flatMap((part) => part.enum ?? (part.items?.enum ? part.items.enum : []))
}

/** What a schema holds a value to, in words: its range, its length, how many items, its default. */
export function limitsOf(schema: Schema | undefined): string[] {
  if (!schema) return []
  const limits: string[] = []
  for (const part of parts(schema).parts) {
    limits.push(...span(part.minimum, part.maximum, ''))
    limits.push(...span(part.minLength, part.maxLength, ' characters'))
    limits.push(...span(part.minItems, part.maxItems, ' items'))
  }
  if (schema.default !== undefined && schema.default !== null && schema.default !== '')
    limits.push(`default ${JSON.stringify(schema.default)}`)
  return limits
}

function span(least: number | undefined, most: number | undefined, unit: string): string[] {
  if (least !== undefined && most !== undefined) return [`${least} to ${most}${unit}`]
  if (most !== undefined) return [`at most ${most}${unit}`]
  // At least one character is any text's, which says nothing worth a word.
  if (least !== undefined && !(least === 1 && unit === ' characters')) return [`at least ${least}${unit}`]
  return []
}

/** The fields of an object's schema, the required first as the contract lists them, then the rest. */
export function fieldsOf(document: OpenApi, schema: Schema): Field[] {
  const object = resolved(document, schema)
  const required = new Set(object.required ?? [])
  return Object.entries(object.properties ?? {})
    .map(([name, property]) => ({
      name,
      type: typeOf(property),
      required: required.has(name),
      description: property.description ?? '',
      values: valuesOf(property),
      limits: limitsOf(property),
    }))
    .sort((a, b) => Number(b.required) - Number(a.required))
}

/** A parameter as a field, its words from the parameter or else from its schema. */
export function parameterField(parameter: Parameter): Field {
  return {
    name: parameter.name,
    type: typeOf(parameter.schema),
    required: parameter.required ?? false,
    description: parameter.description ?? parameter.schema?.description ?? '',
    values: valuesOf(parameter.schema),
    limits: limitsOf(parameter.schema),
  }
}

/** A text of the contract as the page shows it: its lines joined, `code` set apart. */
export function spans(text: string): { code: boolean; text: string }[] {
  return text
    .replace(/\s*\n\s*/g, ' ')
    .trim()
    .split('`')
    .map((part, index) => ({ code: index % 2 === 1, text: part }))
    .filter((part) => part.text !== '')
}

/** A value a schema takes, to show in an example: its own example, else one of its type. */
export function exampleOf(document: OpenApi, schema: Schema | undefined, depth = 0): unknown {
  if (!schema) return null
  if (schema.examples?.length) return schema.examples[0]
  const { parts: kept } = parts(schema)
  const part = kept[0] ?? schema
  if (part !== schema) return exampleOf(document, { ...part, examples: schema.examples }, depth)
  if (refName(schema)) return depth > 3 ? {} : exampleOf(document, resolved(document, schema), depth + 1)
  if (schema.const !== undefined) return schema.const
  if (schema.enum?.length) return schema.enum[0]
  if (schema.default !== undefined && schema.default !== null) return schema.default
  if (schema.type === 'array') return [exampleOf(document, schema.items, depth)]
  if (schema.type === 'integer' || schema.type === 'number') return schema.minimum ?? 1
  if (schema.type === 'boolean') return true
  if (schema.type === 'object' || schema.properties) {
    if (depth > 3) return {}
    const sent = sentFields(schema)
    return Object.fromEntries(sent.map((name) => [name, exampleOf(document, schema.properties?.[name], depth + 1)]))
  }
  if (schema.format === 'date-time') return '2026-10-25T09:30:00Z'
  if (schema.format === 'date') return '2026-10-25'
  return '…'
}

/** The fields an example of a request's body sends: the required ones, or those with an example, or the first. */
function sentFields(schema: Schema): string[] {
  const properties = Object.keys(schema.properties ?? {})
  if (schema.required?.length) return schema.required
  const shown = properties.filter((name) => schema.properties?.[name].examples?.length)
  return shown.length ? shown : properties.slice(0, 1)
}

/** A string in a shell's single quotes, as it is sent. */
function quoted(text: string): string {
  return `'${text.replace(/'/g, `'\\''`)}'`
}

/** The shell variable a path parameter is written as in an example: `project_id` is `$PROJECT_ID`. */
export function variableOf(name: string): string {
  return `$${name.replace(/[^A-Za-z0-9]+/g, '_').toUpperCase()}`
}

/**
 * The operation as a curl command against `base`, the deployment's address: its path's
 * parameters as shell variables, its key from `EMUDOC_API_KEY`, the required query parameters,
 * an Idempotency-Key where it takes one, and an example body.
 */
export function curlOf(document: OpenApi, operation: Operation, base: string): string {
  const path = operation.path.replace(/\{([^}]+)\}/g, (_, name: string) => variableOf(name))
  const options: string[] = []
  const answers = [...operation.answers, ...shared(document)]
  const success = answers.find((answer) => answer.status.startsWith('2') && answer.media)
  if (success?.media === 'text/event-stream') options.push('-N')
  else if (success?.media && success.media !== JSON_MEDIA) options.push('-L -OJ')
  if (operation.method !== 'GET') options.push(`-X ${operation.method}`)
  const lines = [`curl ${[...options, `"${base}${path}"`].join(' ')}`]
  const query = operation.parameters.filter((parameter) => parameter.in === 'query' && parameter.required)
  if (query.length) {
    lines[0] = lines[0].replace(/^curl /, 'curl -G ')
    for (const parameter of query) lines.push(`--data-urlencode ${quoted(`${parameter.name}=${plain(exampleOf(document, parameter.schema))}`)}`)
  }
  for (const name of operation.security) {
    const scheme = document.components?.securitySchemes?.[name]
    if (scheme?.type === 'http' && scheme.scheme === 'bearer') lines.push(`-H "Authorization: Bearer $${KEY_VARIABLE}"`)
  }
  for (const parameter of operation.parameters.filter((found) => found.in === 'header')) {
    if (parameter.name.toLowerCase() === 'idempotency-key') lines.push(`-H "${parameter.name}: $(uuidgen)"`)
    else if (parameter.required) lines.push(`-H ${quoted(`${parameter.name}: ${plain(exampleOf(document, parameter.schema))}`)}`)
  }
  const body = operation.body
  if (body?.media === JSON_MEDIA) {
    lines.push('-H "Content-Type: application/json"')
    lines.push(`-d ${quoted(JSON.stringify(exampleOf(document, body.schema)))}`)
  } else if (body?.media === 'multipart/form-data') {
    const form = resolved(document, body.schema)
    for (const name of sentFields(form)) {
      const field = form.properties?.[name]
      const file = typeOf(field).some((word) => typeof word === 'string' && word.includes('file'))
      lines.push(`-F ${quoted(`${name}=${file ? '@document.pdf' : plain(exampleOf(document, field))}`)}`)
    }
  }
  return lines.join(' \\\n  ')
}

/** A value as a form, a query or a header carries it: text as it is, anything else as JSON. */
function plain(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

/** The deployment's address, as the contract it serves names it, else the page's own. */
export function baseOf(document: OpenApi, origin: string): string {
  return (document.servers?.[0]?.url ?? origin).replace(/\/$/, '')
}
