module.exports = {
  rules: {
    'color-no-invalid-hex': true,
    'property-no-unknown': true,
    'unit-no-unknown': true,
    'block-no-empty': true,
    'declaration-block-no-duplicate-properties': [true, { ignore: ['consecutive-duplicates-with-different-values'] }],
  },
};
