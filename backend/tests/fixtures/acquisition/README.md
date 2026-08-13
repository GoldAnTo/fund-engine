# Official exchange announcement fixtures

Retrieval date: **2026-08-13**. Both captures were read-only requests sent
directly to the official exchange APIs. No generic search proxy or third-party
provider was used. The committed JSON files contain exact field-level subsets
of the response bodies; headers, cookies, unrelated fields, and unrelated
records are omitted. No credentials were sent or stored.

## SSE

- Official page: `https://www.sse.com.cn/disclosure/listedinfo/announcement/`
- API: `https://query.sse.com.cn/security/stock/queryCompanyBulletin.do`
- Method: `GET`
- Request parameters (no secret values were present):
  `isPagination=true`, `productId=688256`, `keyWord=`,
  `securityType=0101,120100,020100,020200,120200`, `reportType2=DQBG`,
  `reportType=ALL`, `beginDate=2025-04-18`, `endDate=2025-04-20`,
  `pageHelp.pageSize=2`, `pageHelp.pageNo=1`, `pageHelp.beginPage=1`,
  `pageHelp.endPage=1`, `pageHelp.cacheSize=1`.
- Response treatment: `sse-announcements.json` is an exact subset of the full
  JSON response. It retains the pagination fields and the four required source
  fields from each of the two returned records. The response supplied no
  distinct announcement/provider ID; the production adapter therefore uses
  the specified deterministic identity fallback.
- Production search intentionally sends `reportType2=` with `reportType=ALL`
  to search all announcement types. The retained real fixture was captured
  with `reportType2=DQBG` and remains periodic-report mapping evidence; it was
  not recaptured or altered for the production search change.
- SHA-256 of the complete, unredacted response bytes actually retrieved:
  `dcd28fc661e0d546d6982bb5edd2423db7932826e053308072ca9968c5105eed`.

## SZSE

- Official page: `https://www.szse.cn/disclosure/listed/notice/index.html`
- API: `https://www.szse.cn/api/disc/announcement/annList`
- Method: `POST` with JSON.
- Request body (no secret values were present):
  `{"seDate":["2025-04-15","2025-04-30"],"stock":["300750"],"channelCode":["listedNotice_disc"],"pageSize":2,"pageNum":1}`.
- Response treatment: `szse-announcements.json` is an exact subset of the full
  JSON response. It retains `announceCount` and the official identity,
  publication, security, title, and attachment fields from each of the two
  returned records. All unrelated fields are omitted.
- SHA-256 of the complete, unredacted response bytes actually retrieved:
  `355d77e129c5bf7abfccb7b7fbfaf1532db4ed6a11ad746a26ef56b606e87c3f`.
