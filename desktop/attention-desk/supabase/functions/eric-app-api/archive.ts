// Restored from the deployed eric-notes-ingest v13 bundle; preserves its durable archive protocol.
import { AppFault, CloudStore, PART_BYTES, hash, stable } from './store.ts';

export function shortcutArchive(store: CloudStore) {
  return async function archive(value: any) {
    const { row, installation } = value;
    const workspaces = await store.db("eric_app_workspaces?notes_installation_id=eq." + installation + "&limit=1");
    if (!workspaces[0]) throw new AppFault(503, "archive_not_ready", "\u7B14\u8BB0\u6682\u672A\u786E\u8BA4\u957F\u671F\u4FDD\u5B58\uFF0C\u8BF7\u4FDD\u7559\u5F85\u53D1\u9001\u6587\u4EF6\u3002");
    const workspace = workspaces[0].id;
    const existing = await store.db("eric_app_captures?workspace_id=eq." + workspace + "&id=eq." + row.id + "&limit=1");
    if (existing[0] && existing[0].state !== "uploading") {
      if (existing[0].source !== "shortcut" || existing[0].files.length !== 1 || existing[0].files[0].sha256 !== row.sha256 || existing[0].files[0].bytes !== row.bytes) throw new AppFault(409, "archive_conflict", "\u63A5\u6536\u8BB0\u5F55\u4E0D\u4E00\u81F4\uFF0C\u539F\u4EF6\u4FDD\u7559\u3002");
      await store.resource(workspace, existing[0].files[0].resource_id);
      return;
    }
    let body = value.body;
    if (!body) {
      const expected = installation + "/" + row.sha256 + ".pdf";
      if (row.object_path !== expected) throw new AppFault(503, "invalid_transport_record", "\u4E91\u7AEF\u539F\u4EF6\u9700\u8981\u6838\u5BF9\uFF0C\u6682\u672A\u6E05\u7406\u3002");
      const response = await store.fetch("/storage/v1/object/eric-notes-transit/" + expected);
      if (!response.ok) throw new AppFault(503, "archive_unavailable", "\u4E91\u7AEF\u539F\u4EF6\u6682\u672A\u786E\u8BA4\u957F\u671F\u4FDD\u5B58\u3002");
      body = new Uint8Array(await response.arrayBuffer());
    }
    if (body.length !== row.bytes || await hash(body) !== row.sha256) throw new AppFault(409, "archive_hash_mismatch", "\u4E91\u7AEF\u539F\u4EF6\u6821\u9A8C\u672A\u901A\u8FC7\uFF0C\u6682\u672A\u6E05\u7406\u3002");
    const filename = existing[0]?.files[0]?.filename || row.filename || value.filename || "\u8BFE\u5802\u7B14\u8BB0.pdf";
    const file = { resource_id: "capture:" + row.id + ":" + row.sha256, sha256: row.sha256, bytes: row.bytes, filename, mime: "application/pdf", part_count: Math.ceil(row.bytes / PART_BYTES) };
    const capture = existing[0] || { id: row.id, device_id: row.device_id, source: "shortcut", title: filename, text_content: "", purpose: "lecture_note", files: [file], course_candidates: [], shared_at: row.shared_at || value.sharedAt || row.received_at };
    const request_hash = existing[0]?.request_hash || await hash(stable(capture));
    await store.rpc("reserve_capture", { p_workspace: workspace, p_capture: { ...capture, request_hash } });
    await store.rpc("register_resource", { p_workspace: workspace, p_resource: { ...file, id: file.resource_id } });
    for (let offset = 0, index = 0; offset < body.length; offset += PART_BYTES, index++) await store.putPart(workspace, file.resource_id, index, body.subarray(offset, Math.min(offset + PART_BYTES, body.length)));
    await store.rpc("finish_resource", { p_workspace: workspace, p_id: file.resource_id });
    const confirmed = await store.rpc("commit_capture", { p_workspace: workspace, p_id: row.id });
    if (!confirmed[0]?.received_at) throw new AppFault(503, "archive_unconfirmed", "\u5C1A\u672A\u786E\u8BA4\u957F\u671F\u4FDD\u5B58\uFF0C\u8BF7\u4FDD\u7559\u5F85\u53D1\u9001\u6587\u4EF6\u3002");
  };
}
