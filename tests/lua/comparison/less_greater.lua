-- EXPECT: 3
local count = 0
if 5 < 10 then count = count + 1 end
if 10 > 5 then count = count + 1 end
if 5 <= 5 then count = count + 1 end
return count
