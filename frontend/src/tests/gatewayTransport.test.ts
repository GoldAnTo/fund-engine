import { afterEach, describe, expect, it, vi } from "vitest";
import {
  GatewayHttpError,
  GatewaySchemaError,
  HttpGatewayClient,
} from "@/gateway/HttpGatewayClient";

const API_BASE = "https://gateway.example.test/api/v1";
const CONVERSATION_ID = "11111111-1111-4111-8111-111111111111";
const INTENT_ID = "22222222-2222-4222-8222-222222222222";
const RUN_SPEC_ID = "33333333-3333-4333-8333-333333333333";
const NATIVE_CASE_ID = "44444444-4444-4444-8444-444444444444";
const NATIVE_RUN_ID = "55555555-5555-4555-8555-555555555555";
const ARTIFACT_ID = "66666666-6666-4666-8666-666666666666";

const receiptWire = {
  conversation_id: CONVERSATION_ID,
  intent_id: INTENT_ID,
  run_spec_id: RUN_SPEC_ID,
  native_case_id: NATIVE_CASE_ID,
  native_run_id: NATIVE_RUN_ID,
  status: "queued",
  latest_sequence: 4,
};

const safeRoleEventWire = {
  sequence: 5,
  run_spec_id: RUN_SPEC_ID,
  role: "sources_evidence",
  type: "source_waiting",
  status: "blocked",
  summary: "Waiting for approved source",
  reason_code: "source_unavailable",
  artifacts: [
    {
      kind: "evidence_link",
      id: ARTIFACT_ID,
      case_id: NATIVE_CASE_ID,
      locator_available: true,
    },
  ],
  occurred_at: "2026-09-05T00:00:00Z",
};

const snapshotWire = {
  conversation_id: CONVERSATION_ID,
  title: "测试研究",
  created_at: "2026-09-05T00:00:00Z",
  updated_at: "2026-09-05T00:00:00Z",
  latest_sequence: 4,
  event_retention_floor: 1,
  messages: [],
  runs: [],
  roles: [],
  events: [],
};

const runScopeWire = {
  topic: "研究设备验证与收入兑现",
  subject_label: null,
  evidence_cutoff_at: "2026-09-05T00:00:00Z",
  case_evidence_cutoff_date: null,
  source_policy: {
    input_kind: "topic",
    allowed_source_types: [],
    allowed_source_roles: ["company_disclosure", "licensed_provider"],
    has_intake_material: false,
  },
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function sseResponse(frames: string): Response {
  return new Response(frames, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function cancellableSseResponse(frames: string, onCancel: () => void): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(frames));
      },
      cancel() {
        onCancel();
      },
    }),
    {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    },
  );
}

function makeClient(fetchMock: ReturnType<typeof vi.fn>) {
  return new HttpGatewayClient({
    baseUrl: API_BASE,
    getAuthorization: () => "Bearer gateway-test-token",
    fetch: fetchMock as unknown as typeof fetch,
    reconnect: { initialDelayMs: 0, maxDelayMs: 0 },
  });
}

function fetchCall(
  fetchMock: ReturnType<typeof vi.fn>,
  index = 0,
): [string, RequestInit] {
  const call = fetchMock.mock.calls[index];
  if (!call) {
    throw new Error(`expected fetch call ${index}`);
  }
  return [String(call[0]), (call[1] ?? {}) as RequestInit];
}

function headersFor(init: RequestInit): Headers {
  return new Headers(init.headers);
}

afterEach(() => vi.useRealTimers());

describe("HttpGatewayClient", () => {
  it("bounds an unresponsive JSON request and aborts the transport without retrying a write", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));
    const client = makeClient(fetchMock);
    const settled = vi.fn();
    const request = client.startConversation({ initialMessage: "研究订单", idempotencyKey: "timeout-key" });
    void request.then(settled, settled);

    await vi.advanceTimersByTimeAsync(30_000);

    expect(settled).toHaveBeenCalledOnce();
    await expect(request).rejects.toMatchObject({ name: "GatewayTimeoutError" });
    expect(fetchCall(fetchMock)[1].signal?.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("includes stalled JSON response bodies in the request deadline", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(new Response(new ReadableStream(), {
      headers: { "Content-Type": "application/json" },
    }));
    const client = makeClient(fetchMock);
    const settled = vi.fn();
    const request = client.getSnapshot(CONVERSATION_ID);
    void request.then(settled, settled);

    await vi.advanceTimersByTimeAsync(30_000);

    expect(settled).toHaveBeenCalledOnce();
    await expect(request).rejects.toMatchObject({ name: "GatewayTimeoutError" });
    expect(fetchCall(fetchMock)[1].signal?.aborted).toBe(true);
  });

  it("marks a closed stream reconnecting before waiting for backoff", async () => {
    const controller = new AbortController();
    const connections: string[] = [];
    const statesDuringBackoff: string[] = [];
    const client = new HttpGatewayClient({
      fetch: vi.fn().mockResolvedValue(sseResponse(": keepalive\n\n")),
      sleep: async () => {
        statesDuringBackoff.push(connections.at(-1)!);
        controller.abort();
      },
    });

    await client.streamEvents({
      conversationId: CONVERSATION_ID,
      afterSequence: 4,
      signal: controller.signal,
      onEvent: vi.fn(),
      onConnection: (state) => connections.push(state),
    });

    expect(statesDuringBackoff).toEqual(["reconnecting"]);
  });

  it("cancels an idle SSE reader promptly when local observation is aborted", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const onCancel = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(cancellableSseResponse(": keepalive\n\n", onCancel));
    const client = makeClient(fetchMock);
    const settled = vi.fn();
    const observation = client.streamEvents({
      conversationId: CONVERSATION_ID,
      afterSequence: 4,
      signal: controller.signal,
      onEvent: vi.fn(),
    });
    void observation.then(settled, settled);
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await vi.advanceTimersByTimeAsync(0);

    expect(onCancel).toHaveBeenCalledOnce();
    expect(settled).toHaveBeenCalledOnce();
    await expect(observation).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("forwards team_progress without advancing the safe event cursor", async () => {
    const controller = new AbortController();
    const onTeamProgress = vi.fn(() => controller.abort());
    const onEvent = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        [
          "event: team_progress",
          `data: ${JSON.stringify({ run_spec_id: RUN_SPEC_ID, revision: 2, event_sequence: 11, status: "active" })}`,
          "",
          "",
        ].join("\n"),
      ),
    );

    await makeClient(fetchMock).streamEvents({
      conversationId: CONVERSATION_ID,
      afterSequence: 4,
      signal: controller.signal,
      onEvent,
      onTeamProgress,
    });

    expect(onTeamProgress).toHaveBeenCalledWith({
      runSpecId: RUN_SPEC_ID,
      revision: 2,
      eventSequence: 11,
      status: "active",
    });
    expect(onEvent).not.toHaveBeenCalled();
    expect(fetchCall(fetchMock)[0]).toContain("after_sequence=4");
  });

  it("starts a conversation with the authenticated JSON receipt contract", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(receiptWire, 201));
    const client = makeClient(fetchMock);

    const receipt = await client.startConversation({
      initialMessage: "研究光模块行业需求变化",
      idempotencyKey: "start-44d72c8b",
    });

    const [url, init] = fetchCall(fetchMock);
    expect(url).toBe(`${API_BASE}/research-conversations`);
    expect(init.method).toBe("POST");
    expect(init.cache).toBe("no-store");
    expect(headersFor(init).get("Authorization")).toBe("Bearer gateway-test-token");
    expect(headersFor(init).get("Content-Type")).toBe("application/json");
    expect(headersFor(init).get("Idempotency-Key")).toBe("start-44d72c8b");
    expect(JSON.parse(String(init.body))).toEqual({
      initial_message: "研究光模块行业需求变化",
    });
    expect(receipt).toEqual({
      conversationId: CONVERSATION_ID,
      intentId: INTENT_ID,
      runSpecId: RUN_SPEC_ID,
      nativeCaseId: NATIVE_CASE_ID,
      nativeRunId: NATIVE_RUN_ID,
      status: "queued",
      latestSequence: 4,
    });
  });

  it("calls the browser default fetch with its global receiver", async () => {
    const receiverSensitiveFetch = vi.fn(function (this: unknown) {
      if (this !== globalThis) {
        throw new TypeError("Illegal invocation");
      }
      return Promise.resolve(
        jsonResponse({ tenant_id: "team-a", roles: [], subject_id: "alice" }),
      );
    });
    const originalFetch = globalThis.fetch;
    vi.stubGlobal("fetch", receiverSensitiveFetch);
    try {
      const client = new HttpGatewayClient({ baseUrl: API_BASE });
      await expect(client.getSession()).resolves.toEqual({
        tenantId: "team-a",
        roles: [],
        subjectId: "alice",
      });
    } finally {
      vi.stubGlobal("fetch", originalFetch);
    }
  });

  it("maps a rejected intent receipt without inventing a native run", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        receipt_kind: "intent",
        intent_id: INTENT_ID,
        conversation_id: CONVERSATION_ID,
        intent_kind: "unsupported",
        outcome: "rejected",
        reason_code: "unsupported_in_gateway_p0",
      }),
    );
    const client = makeClient(fetchMock);

    const receipt = await client.startConversation({
      initialMessage: "暂停当前研究",
      idempotencyKey: "reject-44d72c8b",
    });

    expect(receipt).toEqual({
      receiptKind: "intent",
      intentId: INTENT_ID,
      conversationId: CONVERSATION_ID,
      intentKind: "unsupported",
      outcome: "rejected",
      reasonCode: "unsupported_in_gateway_p0",
    });
    expect("nativeRunId" in receipt).toBe(false);
  });

  it("rejects a snapshot whose conversation identity differs from its requested route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ ...snapshotWire, conversation_id: "other-conversation" }),
    );
    const client = makeClient(fetchMock);

    await expect(client.getSnapshot(CONVERSATION_ID)).rejects.toBeInstanceOf(
      GatewaySchemaError,
    );
  });

  it("decodes the frozen run scope without accepting raw intake fields", async () => {
    const runWire = {
      run_spec_id: RUN_SPEC_ID,
      native_case_id: NATIVE_CASE_ID,
      native_run_id: NATIVE_RUN_ID,
      status: "waiting_for_sources",
      parent_run_spec_id: null,
      created_at: "2026-09-05T00:00:00Z",
      scope: runScopeWire,
    };
    const client = makeClient(vi.fn().mockResolvedValue(
      jsonResponse({ ...snapshotWire, runs: [runWire] }),
    ));

    await expect(client.getSnapshot(CONVERSATION_ID)).resolves.toMatchObject({
      runs: [{
        runSpecId: RUN_SPEC_ID,
        scope: {
          topic: "研究设备验证与收入兑现",
          subjectLabel: null,
          evidenceCutoffAt: "2026-09-05T00:00:00Z",
          caseEvidenceCutoffDate: null,
          sourcePolicy: {
            inputKind: "topic",
            allowedSourceTypes: [],
            allowedSourceRoles: ["company_disclosure", "licensed_provider"],
            hasIntakeMaterial: false,
          },
        },
      }],
    });

    const unsafe = makeClient(vi.fn().mockResolvedValue(
      jsonResponse({ ...snapshotWire, runs: [{ ...runWire, raw_input: "hidden" }] }),
    ));
    await expect(unsafe.getSnapshot(CONVERSATION_ID)).rejects.toBeInstanceOf(
      GatewaySchemaError,
    );
  });

  it("uses authenticated fetch SSE, ignores heartbeats, and parses multiline safe role events", async () => {
    const controller = new AbortController();
    const seen: unknown[] = [];
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        [
          ": keepalive",
          "",
          "id: 5",
          "event: role_event",
          'data: {"sequence":5,',
          'data: "run_spec_id":"33333333-3333-4333-8333-333333333333","role":"sources_evidence","type":"source_waiting","status":"blocked","summary":"Waiting for approved source","reason_code":"source_unavailable","artifacts":[{"kind":"evidence_link","id":"66666666-6666-4666-8666-666666666666","case_id":"44444444-4444-4444-8444-444444444444","locator_available":true}],"occurred_at":"2026-09-05T00:00:00Z"}',
          "",
          "",
        ].join("\n"),
      ),
    );
    const client = makeClient(fetchMock);

    await client.streamEvents({
      conversationId: CONVERSATION_ID,
      afterSequence: 4,
      signal: controller.signal,
      onEvent(event) {
        seen.push(event);
        controller.abort();
      },
    });

    const [url, init] = fetchCall(fetchMock);
    expect(url).toBe(
      `${API_BASE}/research-conversations/${CONVERSATION_ID}/events?after_sequence=4`,
    );
    expect(init.method).toBe("GET");
    expect(headersFor(init).get("Authorization")).toBe("Bearer gateway-test-token");
    expect(headersFor(init).get("Accept")).toBe("text/event-stream");
    expect(headersFor(init).get("Last-Event-ID")).toBe("4");
    expect(seen).toEqual([
      {
        sequence: 5,
        runSpecId: RUN_SPEC_ID,
        role: "sources_evidence",
        type: "source_waiting",
        status: "blocked",
        summary: "Waiting for approved source",
        reasonCode: "source_unavailable",
        artifacts: [
          {
            kind: "evidence_link",
            id: ARTIFACT_ID,
            caseId: NATIVE_CASE_ID,
            locatorAvailable: true,
          },
        ],
        occurredAt: "2026-09-05T00:00:00Z",
      },
    ]);
  });

  it("forwards snapshot_required to the caller without treating it as a role event", async () => {
    const controller = new AbortController();
    const snapshots: unknown[] = [];
    const onEvent = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        [
          "event: snapshot_required",
          `data: {"conversation_id":"${CONVERSATION_ID}","latest_sequence":7}`,
          "",
          "",
        ].join("\n"),
      ),
    );
    const client = makeClient(fetchMock);

    await client.streamEvents({
      conversationId: CONVERSATION_ID,
      afterSequence: 2,
      signal: controller.signal,
      onEvent,
      onSnapshotRequired(snapshot) {
        snapshots.push(snapshot);
        controller.abort();
      },
    });

    expect(snapshots).toEqual([
      { conversationId: CONVERSATION_ID, latestSequence: 7 },
    ]);
    expect(onEvent).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reconnects from the last accepted event cursor and abort only stops local observation", async () => {
    const controller = new AbortController();
    const seen: unknown[] = [];
    const secondEvent = {
      ...safeRoleEventWire,
      sequence: 6,
      occurred_at: "2026-09-05T00:01:00Z",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        sseResponse(
          [
            "id: 5",
            "event: role_event",
            `data: ${JSON.stringify(safeRoleEventWire)}`,
            "",
            "",
          ].join("\n"),
        ),
      )
      .mockResolvedValueOnce(
        sseResponse(
          [
            "id: 6",
            "event: role_event",
            `data: ${JSON.stringify(secondEvent)}`,
            "",
            "",
          ].join("\n"),
        ),
      );
    const client = makeClient(fetchMock);

    await client.streamEvents({
      conversationId: CONVERSATION_ID,
      afterSequence: 4,
      signal: controller.signal,
      onEvent(event) {
        seen.push(event);
        if ((event as { sequence: number }).sequence === 6) {
          controller.abort();
        }
      },
    });

    const [firstUrl, firstInit] = fetchCall(fetchMock, 0);
    const [secondUrl, secondInit] = fetchCall(fetchMock, 1);
    expect(firstUrl).toContain("after_sequence=4");
    expect(headersFor(firstInit).get("Last-Event-ID")).toBe("4");
    expect(secondUrl).toContain("after_sequence=5");
    expect(headersFor(secondInit).get("Last-Event-ID")).toBe("5");
    expect(seen).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(controller.signal.aborted).toBe(true);
    expect(
      fetchMock.mock.calls.every(
        ([, init]) => (init as RequestInit).method === "GET",
      ),
    ).toBe(true);
  });

  it("rejects unsafe raw event fields instead of forwarding them to the workbench", async () => {
    const onEvent = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        [
          "id: 5",
          "event: role_event",
          `data: ${JSON.stringify({ ...safeRoleEventWire, prompt: "hidden prompt", tool_arguments: { token: "secret" } })}`,
          "",
          "",
        ].join("\n"),
      ),
    );
    const client = makeClient(fetchMock);

    await expect(
      client.streamEvents({
        conversationId: CONVERSATION_ID,
        afterSequence: 4,
        onEvent,
      }),
    ).rejects.toThrow(/safe|schema|unexpected|unsafe/i);

    expect(onEvent).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("treats access_revoked as terminal and delegates private-projection clearing to the caller", async () => {
    const onAccessRevoked = vi.fn();
    const onStreamError = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        [
          "event: stream_error",
          'data: {"code":"access_revoked"}',
          "",
          "",
        ].join("\n"),
      ),
    );
    const client = makeClient(fetchMock);

    await client.streamEvents({
      conversationId: CONVERSATION_ID,
      afterSequence: 4,
      onEvent: vi.fn(),
      onAccessRevoked,
      onStreamError,
    });

    expect(onAccessRevoked).toHaveBeenCalledOnce();
    expect(onStreamError).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("pauses cleanly for projection_unavailable without forwarding raw data", async () => {
    const onEvent = vi.fn();
    const onStreamError = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        [
          "event: stream_error",
          'data: {"code":"projection_unavailable"}',
          "",
          "",
        ].join("\n"),
      ),
    );
    const client = makeClient(fetchMock);

    await client.streamEvents({
      conversationId: CONVERSATION_ID,
      afterSequence: 4,
      onEvent,
      onStreamError,
    });

    expect(onEvent).not.toHaveBeenCalled();
    expect(onStreamError).toHaveBeenCalledWith("projection_unavailable");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("cancels the local SSE reader after a terminal stream outcome", async () => {
    const onCancel = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      cancellableSseResponse(
        [
          "event: stream_error",
          'data: {"code":"projection_unavailable"}',
          "",
          "",
        ].join("\n"),
        onCancel,
      ),
    );
    const client = makeClient(fetchMock);

    await client.streamEvents({
      conversationId: CONVERSATION_ID,
      afterSequence: 4,
      onEvent: vi.fn(),
      onStreamError: vi.fn(),
    });

    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("automatically reconnects after an ordinary transport failure from the last safe cursor", async () => {
    const controller = new AbortController();
    const onEvent = vi.fn(() => controller.abort());
    const connections: string[] = [];
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network unavailable"))
      .mockResolvedValueOnce(
        sseResponse(
          [
            "id: 5",
            "event: role_event",
            `data: ${JSON.stringify(safeRoleEventWire)}`,
            "",
            "",
          ].join("\n"),
        ),
      );
    const client = makeClient(fetchMock);

    await client.streamEvents({
      conversationId: CONVERSATION_ID,
      afterSequence: 4,
      signal: controller.signal,
      onEvent,
      onConnection: (state) => connections.push(state),
    });

    expect(onEvent).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchCall(fetchMock, 1)[0]).toContain("after_sequence=4");
    expect(connections).toContain("reconnecting");
  });

  it("surfaces a stale cursor response as a typed terminal HTTP error", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "after_sequence and Last-Event-ID must match" }, 422),
    );
    const client = makeClient(fetchMock);

    let thrown: unknown;
    try {
      await client.streamEvents({
        conversationId: CONVERSATION_ID,
        afterSequence: 4,
        onEvent: vi.fn(),
      });
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(GatewayHttpError);
    expect(thrown).toMatchObject({ status: 422 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("SSE connection watchdog", () => {
  function watch(fetchMock: ReturnType<typeof vi.fn>) {
    const controller = new AbortController();
    const states: string[] = [];
    const client = new HttpGatewayClient({ fetch: fetchMock as unknown as typeof fetch, reconnect: { initialDelayMs: 400, maxDelayMs: 400 } });
    const onEvent = vi.fn();
    const observation = client.streamEvents({ conversationId: CONVERSATION_ID, afterSequence: 4, signal: controller.signal, onEvent, onConnection: state => states.push(state) });
    return { controller, states, observation, onEvent };
  }

  it("aborts a headerless fetch at 30s and remains reconnecting during actual backoff", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));
    const active = watch(fetchMock);
    await vi.advanceTimersByTimeAsync(29_999);
    expect(active.states).toEqual(["connecting"]);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchCall(fetchMock)[1].signal?.aborted).toBe(true);
    expect(active.states.at(-1)).toBe("reconnecting");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(399);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    active.controller.abort();
    await active.observation;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("times out a headers-only stalled body at 45s, not a total 30s SSE deadline", async () => {
    vi.useFakeTimers();
    const cancel = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(new Response(new ReadableStream({ cancel }), { headers: { "Content-Type": "text/event-stream" } }));
    const active = watch(fetchMock);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(active.states.at(-1)).toBe("live");
    expect(fetchCall(fetchMock)[1].signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(active.states.at(-1)).toBe("reconnecting");
    expect(cancel).toHaveBeenCalledOnce();
    active.controller.abort();
    await active.observation;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("heartbeats renew idle time and half-open recovery resumes only applied events", async () => {
    vi.useFakeTimers();
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ start(value) { stream = value; }, cancel });
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(body, { headers: { "Content-Type": "text/event-stream" } })).mockImplementation(() => new Promise<Response>(() => undefined));
    const active = watch(fetchMock);
    const encoder = new TextEncoder();
    await vi.advanceTimersByTimeAsync(0);
    stream.enqueue(encoder.encode(`id: 5\nevent: role_event\ndata: ${JSON.stringify(safeRoleEventWire)}\n\n`));
    await vi.advanceTimersByTimeAsync(0);
    for (let index = 0; index < 8; index++) {
      await vi.advanceTimersByTimeAsync(15_000);
      stream.enqueue(encoder.encode(": heartbeat\n\n"));
      await vi.advanceTimersByTimeAsync(0);
      expect(active.states.at(-1)).toBe("live");
      expect(fetchMock).toHaveBeenCalledTimes(1);
    }
    stream.enqueue(encoder.encode("id: 6\nevent: role_event\ndata: {"));
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(45_000);
    expect(cancel).toHaveBeenCalledOnce();
    expect(active.states.at(-1)).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(400);
    expect(fetchCall(fetchMock, 1)[0]).toContain("after_sequence=5");
    expect(headersFor(fetchCall(fetchMock, 1)[1]).get("Last-Event-ID")).toBe("5");
    expect(active.onEvent).toHaveBeenCalledOnce();
    active.controller.abort();
    await active.observation;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("parent cancellation settles a hung handshake and clears its watchdog", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));
    const active = watch(fetchMock);
    const settled = vi.fn();
    void active.observation.then(settled, settled);
    active.controller.abort();
    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toHaveBeenCalledOnce();
    expect(fetchCall(fetchMock)[1].signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([401, 403])("authorization HTTP %s remains terminal and clears all timers", async status => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}, status));
    const active = watch(fetchMock);
    await expect(active.observation).rejects.toMatchObject({ status });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(active.states).not.toContain("reconnecting");
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("SSE watchdog cleanup edges", () => {
  it("a response with no body is a reconnectable closed transport", async () => {
    const parent = new AbortController();
    const states: string[] = [];
    const client = new HttpGatewayClient({
      fetch: vi.fn().mockResolvedValue(new Response(null, { headers: { "Content-Type": "text/event-stream" } })),
      sleep: async () => { expect(states.at(-1)).toBe("reconnecting"); parent.abort(); },
    });
    await client.streamEvents({ conversationId: CONVERSATION_ID, afterSequence: 4, signal: parent.signal, onEvent: vi.fn(), onConnection: state => states.push(state) });
    expect(parent.signal.aborted).toBe(true);
  });

  it("disposes late headers after the handshake was aborted", async () => {
    vi.useFakeTimers();
    let resolveResponse!: (response: Response) => void;
    const parent = new AbortController();
    const cancelled = vi.fn();
    const fetchMock = vi.fn(() => new Promise<Response>(resolve => { resolveResponse = resolve; }));
    const client = new HttpGatewayClient({ fetch: fetchMock, sseHandshakeTimeoutMs: 10, reconnect: { initialDelayMs: 400 } });
    const task = client.streamEvents({ conversationId: CONVERSATION_ID, afterSequence: 4, signal: parent.signal, onEvent: vi.fn() });
    await vi.advanceTimersByTimeAsync(10);
    resolveResponse(cancellableSseResponse(": late\n\n", cancelled));
    await vi.advanceTimersByTimeAsync(0);
    expect(cancelled).toHaveBeenCalledOnce();
    parent.abort();
    await task;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("cleans up even when an underlying stream cancel never settles", async () => {
    vi.useFakeTimers();
    const parent = new AbortController();
    const cancel = vi.fn(() => new Promise<void>(() => undefined));
    const client = new HttpGatewayClient({ fetch: vi.fn().mockResolvedValue(new Response(new ReadableStream({ cancel }), { headers: { "Content-Type": "text/event-stream" } })) });
    const task = client.streamEvents({ conversationId: CONVERSATION_ID, afterSequence: 4, signal: parent.signal, onEvent: vi.fn() });
    const settled = vi.fn();
    void task.then(settled, settled);
    await vi.advanceTimersByTimeAsync(0);
    parent.abort();
    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toHaveBeenCalledOnce();
    expect(cancel).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });
});
