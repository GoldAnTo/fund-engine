import { authenticatedApi, expect, loginThroughKeycloak, test } from "./fixtures/auth";
import { assertLiveRuntimeReady, observeNoMockResponses } from "./helpers/workflow";


test("same-tenant user remains hidden until the owner grants viewer access", async ({ browser }, testInfo) => {
  const baseURL = testInfo.project.use.baseURL;
  if (typeof baseURL !== "string") throw new Error("live baseURL is required");
  const aliceContext = await browser.newContext({
    baseURL,
    recordVideo: { dir: testInfo.outputPath("alice-videos") },
  });
  const bobContext = await browser.newContext({
    baseURL,
    recordVideo: { dir: testInfo.outputPath("bob-videos") },
  });
  const alice = await aliceContext.newPage();
  const bob = await bobContext.newPage();
  const aliceNoMock = observeNoMockResponses(alice);
  const bobNoMock = observeNoMockResponses(bob);
  try {
    const aliceSession = await loginThroughKeycloak(alice, "alice", testInfo);
    const bobSession = await loginThroughKeycloak(bob, "bob", testInfo);
    await assertLiveRuntimeReady(alice, testInfo);

    const eventTitle = `Live permission boundary ${Date.now()}`;
    const created = await authenticatedApi<{ case_id: string }>(
      alice,
      "/api/v1/event-research",
      {
        method: "POST",
        body: {
          raw_input: "A governed permission test Case with a frozen user-supplied intake.",
          source_type: "pasted_snapshot",
          source_metadata: {
            authority_level: "user_supplied",
            permissions: { ai_processing: true, display: true, export: false, api: false },
          },
          event_title: eventTitle,
          research_question: "Can an ungranted user discover this Case?",
          candidate_factors: ["Visibility", "Edit authority", "Audit identity"],
          research_protocol_required: false,
        },
      },
    );
    expect(created.status, created.text).toBe(201);
    const caseId = created.body.case_id;

    const missingWorkflow = await authenticatedApi<{
      error: {
        details: {
          safe_action: { method: string; href: string; payload: Record<string, unknown> };
        };
      };
    }>(alice, `/api/v1/event-research/${caseId}/workflow`);
    expect(missingWorkflow.status, missingWorkflow.text).toBe(409);
    const initialize = missingWorkflow.body.error.details.safe_action;
    const initialized = await authenticatedApi(
      alice,
      initialize.href,
      { method: initialize.method, body: initialize.payload },
    );
    expect(initialized.status, initialized.text).toBe(202);

    const hidden = await authenticatedApi(bob, `/api/v1/event-research/${caseId}/workbench`);
    expect(hidden.status, hidden.text).toBe(404);
    const hiddenWorkflowResponse = bob.waitForResponse((response) =>
      response.url().includes(`/api/v1/event-research/${caseId}/workflow`)
      && response.status() === 404,
    );
    await bob.goto(`/events/${caseId}`);
    await hiddenWorkflowResponse;
    await expect(bob.getByRole("alert")).toContainText("统一研究流程无法读取");
    await expect(bob.getByText(/Live permission boundary/)).toHaveCount(0);

    const granted = await authenticatedApi<{ user_id: string; role: string }>(
      alice,
      `/api/v1/event-research/${caseId}/access-grants`,
      {
        method: "POST",
        body: {
          target_user_id: bobSession.user_id,
          role: "viewer",
          reason: "Live acceptance verifies explicit read-only collaboration.",
        },
      },
    );
    expect(granted.status, granted.text).toBe(201);
    expect(granted.body).toMatchObject({ user_id: bobSession.user_id, role: "viewer" });

    const visible = await authenticatedApi(bob, `/api/v1/event-research/${caseId}/workbench`);
    expect(visible.status, visible.text).toBe(200);
    await bob.goto(`/events/${caseId}`);
    await expect(bob.getByRole("heading", { name: eventTitle })).toBeVisible();
    const deniedWrite = await authenticatedApi(
      bob,
      `/api/v1/event-research/${caseId}/scope`,
      {
        method: "PUT",
        body: {
          factors: ["Visibility", "Edit authority", "Audit identity"],
          change_reason: "Viewer must not be able to write.",
        },
      },
    );
    expect(deniedWrite.status, deniedWrite.text).toBe(403);
    expect(aliceSession.user_id).not.toBe(bobSession.user_id);
    await Promise.all([aliceNoMock.assertClean(), bobNoMock.assertClean()]);
  } finally {
    await aliceContext.close();
    await bobContext.close();
  }
});
