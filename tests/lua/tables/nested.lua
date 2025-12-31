-- EXPECT: 42
local t = {}
t.inner = {}
t.inner.value = 42
return t.inner.value
