#include <gtest/gtest.h>
#include "aemu/base/misc/StringUtils.h"
#include <locale>

using namespace android::base;

class StringUtilsTest : public ::testing::Test {
protected:
    void SetUp() override {
        // Save the old locale and set the new one
        oldLocale = std::locale::global(std::locale("C"));
    }

    void TearDown() override {
        // Restore the old locale
        std::locale::global(oldLocale);
    }

private:
    std::locale oldLocale;
};

TEST_F(StringUtilsTest, StartsWithVariants) {
    EXPECT_TRUE(StartsWith("Hello World", "Hello"));
    EXPECT_TRUE(StartsWith("Hello World", 'H'));
    EXPECT_TRUE(StartsWithIgnoreCase("Hello World", "hello"));
    EXPECT_FALSE(StartsWith("Hello World", "World"));
    EXPECT_FALSE(StartsWith("Hello World", 'W'));
    EXPECT_FALSE(StartsWithIgnoreCase("Hello World", "world"));
}