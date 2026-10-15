/* ES module. Inject Firebase Auth's user.getIdToken() to support renewal and MFA.
   No credentials, tokens, vehicle records or documents are persisted here. */
export class VaultAPI {
  constructor(hosts, getToken) {
    this.hosts = hosts;
    this.getToken = getToken;
  }
  async request(vault, path, { method = 'GET', body, key } = {}) {
    const headers = { Authorization: `Bearer ${await this.getToken()}` };
    if (key) headers['Idempotency-Key'] = key;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const r = await fetch(`${this.hosts[vault]}${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'omit', cache: 'no-store'
    });
    if (!r.ok) throw Object.assign(new Error(`Vault request failed (${r.status})`), { status: r.status });
    return r.status === 204 ? undefined : r.json();
  }
  async all(vault, resource) {
    const items = [];
    let cursor;
    do {
      const page = await this.request(vault, `/v1/${resource}?limit=100${cursor ? `&after=${encodeURIComponent(cursor)}` : ''}`);
      items.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor);
    return items;
  }
  clients() { return this.all('clients', 'clients'); }
  assets() { return this.all('vehicles', 'assets'); }
  // Retain each key for the same logical operation and retries. Never reuse with changed content.
  createClient(body, key) { return this.request('clients', '/v1/clients', { method:'POST', body, key }); }
  createAsset(body, key) { return this.request('vehicles', '/v1/assets', { method:'POST', body, key }); }
  createKycCase(body, key) { return this.request('compliance', '/v1/kyc-cases', { method:'POST', body, key }); }
  createTransaction(body, key) { return this.request('finance', '/v1/transactions', { method:'POST', body, key }); }
  async upload(vault, parentId, file, category, key) {
    if (!['application/pdf', 'image/png', 'image/jpeg'].includes(file.type) || file.size > 20*1024*1024 || !file.size) {
      throw new Error('Use a PDF, JPEG or PNG between 1 byte and 20 MiB');
    }
    const bytes = await file.arrayBuffer();
    const sha256 = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
    const doc = await this.request(vault, '/v1/documents', { method:'POST', key,
      body:{parent_id:parentId, filename:file.name, category, content_type:file.type, size_bytes:file.size, sha256} });
    if (doc.data.status !== 'awaiting_upload') return doc;
    const response = await fetch(`${this.hosts[vault]}/v1/documents/${doc.id}/content`, {
      method:'PUT', headers:{Authorization:`Bearer ${await this.getToken()}`, 'Content-Type':file.type}, body:bytes,
      credentials:'omit', cache:'no-store'
    });
    if (!response.ok) throw Object.assign(new Error(`Upload failed (${response.status})`),{status:response.status, documentId:doc.id});
    return response.json(); // quarantined, not clean; poll metadata for scan result.
  }
  async download(vault, documentId) {
    const r = await fetch(`${this.hosts[vault]}/v1/documents/${documentId}/content`, {
      headers:{Authorization:`Bearer ${await this.getToken()}`}, credentials:'omit', cache:'no-store'
    });
    if (!r.ok) throw Object.assign(new Error(`Download failed (${r.status})`),{status:r.status});
    return r.blob();
  }
}
