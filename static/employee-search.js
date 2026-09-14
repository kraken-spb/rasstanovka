// Keep arbitrary regular expressions off the UI thread; the caller enforces a time limit.
self.onmessage = ({data}) => {
  try {
    const pattern = new RegExp(data.query, 'iu');
    const ids = data.rows.filter(row => row.fields.some(value => pattern.test(value))).map(row => row.id);
    self.postMessage({ids});
  } catch (error) {
    self.postMessage({error: 'Ошибка Regex: ' + error.message});
  }
};
