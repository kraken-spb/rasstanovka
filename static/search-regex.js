(() => {
  'use strict';
  // Commas and whitespace separate alternatives outside character classes and quantifiers.
  window.SearchRegex = {source(query) {
    let result = '', inClass = false, previousSyntax = '';
    for (let i = 0; i < query.length; i++) {
      const char = query[i];
      if (char === '\\') {
        const next = query[++i];
        result += next === ',' || (next !== undefined && /\s/.test(next)) ? next : '\\' + (next ?? '');
      } else if (char === '[') {
        inClass = true; result += char;
      } else if (char === ']') {
        inClass = false; result += char;
      } else if (!inClass && char === '{') {
        const quantifier = query.slice(i).match(/^\{\d+(?:,\d*)?\}/);
        if (quantifier) { result += quantifier[0]; i += quantifier[0].length - 1; }
        else result += char;
      } else if (!inClass && /[,\s]/.test(char)) {
        while (i + 1 < query.length && /[,\s]/.test(query[i + 1])) i++;
        // Ignore edge separators and collapse mixed separator runs into one alternative.
        if (result && i + 1 < query.length && previousSyntax !== '|' && previousSyntax !== '(' && !/[|)]/.test(query[i + 1])) result += '|';
      } else result += char;
      previousSyntax = char === '\\' || inClass ? '' : char;
    }
    return result;
  }};
})();
