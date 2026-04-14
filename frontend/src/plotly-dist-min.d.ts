// Plotly.js dist-min nemá vlastní @types balíček — použijeme typy z plotly.js
declare module 'plotly.js-dist-min' {
  export * from 'plotly.js'
}
