(function () {
  "use strict";

  function indexById(records) {
    return new Map(records.map((record) => [record.id, record]));
  }

  function formatFreezeTime(value) {
    return value.replace("T", " ").slice(0, 16);
  }

  function assertPointInTimeInputs(snapshot, inputs) {
    for (const input of inputs) {
      if (input.availableAt.slice(0, 10) > snapshot.cutoff) {
        throw new RangeError(`${input.id} was unavailable at snapshot ${snapshot.id}`);
      }
    }
  }

  function buildVersionsViewModel(fixture) {
    const comparison = fixture.case.versionComparison;
    const snapshots = indexById(fixture.snapshots);
    const beforeSnapshot = snapshots.get(comparison.beforeSnapshotId);
    const afterSnapshot = snapshots.get(comparison.afterSnapshotId);
    if (!beforeSnapshot || !afterSnapshot) throw new RangeError("Version comparison references an unknown snapshot");
    if (beforeSnapshot.cutoff >= afterSnapshot.cutoff) throw new RangeError("Version comparison must move forward in cutoff time");
    assertPointInTimeInputs(beforeSnapshot, comparison.before.inputs);
    assertPointInTimeInputs(afterSnapshot, comparison.after.inputs);

    const snapshotView = (snapshot, side) => ({
      ...snapshot,
      side,
      freezeTime: formatFreezeTime(snapshot.frozenAt),
    });

    return {
      case: {
        id: fixture.case.id,
        title: fixture.case.title,
        question: fixture.case.question,
        researchObject: fixture.case.researchObject,
      },
      beforeSnapshot: snapshotView(beforeSnapshot, "before"),
      afterSnapshot: snapshotView(afterSnapshot, "after"),
      before: comparison.before,
      after: comparison.after,
      changeRail: comparison.changeRail,
      aiProposal: comparison.aiProposal,
    };
  }

  window.VERSIONS_STATE = Object.freeze({
    buildVersionsViewModel,
  });
}());
