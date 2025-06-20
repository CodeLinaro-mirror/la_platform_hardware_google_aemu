#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include "aemu/base/misc/StringUtils.h"

#include <string>
#include <string_view>

#ifdef __APPLE__
#include <malloc/malloc.h>
#endif


using namespace android::base;
using namespace std::literals::string_view_literals;
using ::testing::ElementsAre;

#include <locale>

using namespace android::base;

class StringUtilsTest : public ::testing::Test {
protected:
    void SetUp() override {
        // Save the old locale and set the new one so we know that
        // ignorecase behaves as expected. Note the locale is set to C
        // locale that uses standard ASCII.
        oldLocale = std::locale::global(std::locale("C"));
    }

    void TearDown() override {
        // Restore the old locale
        std::locale::global(oldLocale);
    }

private:
    std::locale oldLocale;
};


#ifdef __APPLE__
TEST_F(StringUtilsTest, StrDupMinimalAllocationWhenSourceIsNullTerminated) {
    // Buffer with null terminator at the right place
    std::string_view view = "0123456789abcde\0"sv;
    EXPECT_EQ(view.size(), 16);
    EXPECT_EQ(view.data()[15], '\0');
    char* result = strDup(view);
    ASSERT_NE(result, nullptr);
    EXPECT_STREQ(result, "0123456789abcde");
    EXPECT_EQ(result[15], '\0');
    // On macOS, get the actual allocated size, note this assumes the allocator is
    // capable of allocating 16 bytes for the string, if this is not the case, the test will fail.
    size_t actual_size = malloc_size(result);
    EXPECT_EQ(actual_size, 16) << "You should have allocated 16 bytes for the string (double \\000?)";
    free(result);
}
#endif

TEST_F(StringUtilsTest, StrDupFromLiteral) {
    const char* literal = "hello";
    std::string_view view(literal);
    char* result = strDup(view);
    ASSERT_NE(result, nullptr);
    EXPECT_STREQ(result, "hello");
    EXPECT_EQ(result[view.size()], '\0');
    free(result);
}

TEST_F(StringUtilsTest, StrDupFromStdString) {
    std::string s = "world";
    std::string_view view(s);
    char* result = strDup(view);
    ASSERT_NE(result, nullptr);
    EXPECT_STREQ(result, "world");
    EXPECT_EQ(result[view.size()], '\0');
    free(result);
}

TEST_F(StringUtilsTest, StrDupFromNonNullTerminatedBuffer) {
    char buffer[] = {'a', 'b', 'c', 'd', 'e'}; // No null terminator
    std::string_view view(buffer, 5);
    char* result = strDup(view);
    ASSERT_NE(result, nullptr);
    EXPECT_EQ(std::memcmp(result, "abcde", 5), 0);
    EXPECT_EQ(result[5], '\0');
    free(result);
}

TEST_F(StringUtilsTest, StrDupFromBufferWithNullInside) {
    char buffer[] = {'x', 'y', '\0', 'z', 'w'};
    std::string_view view(buffer, 5);
    char* result = strDup(view);
    ASSERT_NE(result, nullptr);
    EXPECT_EQ(result[0], 'x');
    EXPECT_EQ(result[1], 'y');
    EXPECT_EQ(result[2], '\0');
    EXPECT_EQ(result[3], 'z');
    EXPECT_EQ(result[4], 'w');
    EXPECT_EQ(result[5], '\0');
    free(result);
}

TEST_F(StringUtilsTest, StrDupFromEmptyView) {
    std::string_view view;
    char* result = strDup(view);
    ASSERT_NE(result, nullptr);
    EXPECT_EQ(result[0], '\0');
    free(result);
}

TEST_F(StringUtilsTest, StrContains) {
    EXPECT_TRUE(strContains("Hello World", "World"));
    EXPECT_TRUE(strContains("Hello World", "Hello"));
    EXPECT_FALSE(strContains("Hello World", "Goodbye"));
    EXPECT_FALSE(strContains("", "test"));
    EXPECT_TRUE(strContains("test", ""));
}

TEST_F(StringUtilsTest, Trim) {
    EXPECT_EQ(trim("  hello  "), "hello");
    EXPECT_EQ(trim("hello  "), "hello");
    EXPECT_EQ(trim("  hello"), "hello");
    EXPECT_EQ(trim("hello"), "hello");
    EXPECT_EQ(trim(""), "");
    EXPECT_EQ(trim("   "), "");
}

TEST_F(StringUtilsTest, StartsWith) {
    EXPECT_TRUE(startsWith("Hello World", "Hello"));
    EXPECT_TRUE(startsWith("Hello World", ""));
    EXPECT_FALSE(startsWith("Hello World", "World"));
    EXPECT_FALSE(startsWith("", "test"));
}

TEST_F(StringUtilsTest, EndsWith) {
    EXPECT_TRUE(endsWith("Hello World", "World"));
    EXPECT_TRUE(endsWith("Hello World", ""));
    EXPECT_FALSE(endsWith("Hello World", "Hello"));
    EXPECT_FALSE(endsWith("", "test"));
}

TEST_F(StringUtilsTest, SplitString) {
    std::vector<std::string> results;
    auto collect = [&results](const std::string& s) { results.push_back(s); };

    results.clear();
    split<std::string>("a,b,c", ",", collect);
    ASSERT_EQ(results.size(), 3);
    EXPECT_THAT(results, ElementsAre("a", "b", "c"));

    results.clear();
    split<std::string>("a,,c", ",", collect);
    ASSERT_EQ(results.size(), 3);
    EXPECT_THAT(results, ElementsAre("a", "", "c"));
}

TEST_F(StringUtilsTest, SplitTokens) {
    std::vector<std::string> tokens;

    splitTokens("a b c", &tokens, " ");
    ASSERT_EQ(tokens.size(), 3);
    EXPECT_THAT(tokens, ElementsAre("a", "b", "c"));

    tokens.clear();
    splitTokens("a,,c", &tokens, ",");
    ASSERT_EQ(tokens.size(), 3);
    EXPECT_THAT(tokens, ElementsAre("a", "", "c"));
}

TEST_F(StringUtilsTest, SplitComma) {
    auto result = Split("a,b,c", ",");
    ASSERT_EQ(result.size(), 3);
    EXPECT_THAT(result, ElementsAre("a", "b", "c"));

    result = Split("a,,c", ",");
    ASSERT_EQ(result.size(), 3);
    EXPECT_THAT(result, ElementsAre("a", "", "c"));
}

TEST_F(StringUtilsTest, JoinContainer) {
    std::vector<std::string> vec = {"a", "b", "c"};
    EXPECT_EQ(Join(vec, ","), "a,b,c");
    EXPECT_EQ(Join(vec, "-"), "a-b-c");

    std::vector<const char*> cvec = {"a", "b", "c"};
    EXPECT_EQ(Join(cvec, ","), "a,b,c");

    std::vector<const char*> dvec = {"a"};
    EXPECT_EQ(Join(dvec, ","), "a");
}

TEST_F(StringUtilsTest, StartsWithVariants) {
    EXPECT_TRUE(StartsWith("Hello World", "Hello"));
    EXPECT_TRUE(StartsWith("Hello World", 'H'));
    EXPECT_TRUE(StartsWithIgnoreCase("Hello World", "hello"));
    EXPECT_FALSE(StartsWith("Hello World", "World"));
    EXPECT_FALSE(StartsWith("Hello World", 'W'));
    EXPECT_FALSE(StartsWithIgnoreCase("Hello World", "world"));
}

TEST_F(StringUtilsTest, EndsWithVariants) {
    EXPECT_TRUE(EndsWith("Hello World", "World"));
    EXPECT_TRUE(EndsWith("Hello World", 'd'));
    EXPECT_TRUE(EndsWithIgnoreCase("Hello World", "world"));
    EXPECT_FALSE(EndsWith("Hello World", "Hello"));
    EXPECT_FALSE(EndsWith("Hello World", 'H'));
    EXPECT_FALSE(EndsWithIgnoreCase("Hello World", "hello"));
}

TEST_F(StringUtilsTest, EqualsIgnoreCase) {
    EXPECT_TRUE(EqualsIgnoreCase("Hello", "hello"));
    EXPECT_TRUE(EqualsIgnoreCase("HELLO", "hello"));
    EXPECT_TRUE(EqualsIgnoreCase("hello", "HELLO"));
    EXPECT_FALSE(EqualsIgnoreCase("Hello", "World"));
}

TEST_F(StringUtilsTest, ConsumePrefix) {
    std::string_view s = "Hello World"sv;
    EXPECT_TRUE(ConsumePrefix(&s, "Hello "));
    EXPECT_EQ(s, "World");
    EXPECT_FALSE(ConsumePrefix(&s, "Hello"));
    EXPECT_EQ(s, "World");
}

TEST_F(StringUtilsTest, ConsumeSuffix) {
    std::string_view s = "Hello World"sv;
    EXPECT_TRUE(ConsumeSuffix(&s, " World"));
    EXPECT_EQ(s, "Hello");
    EXPECT_FALSE(ConsumeSuffix(&s, "World"));
    EXPECT_EQ(s, "Hello");
}

TEST_F(StringUtilsTest, StringReplace) {
    EXPECT_EQ(StringReplace("Hello World", "World", "Earth", false), "Hello Earth");
    EXPECT_EQ(StringReplace("Hello World World", "World", "Earth", true), "Hello Earth Earth");
    EXPECT_EQ(StringReplace("Hello World", "o", "0", true), "Hell0 W0rld");
    EXPECT_EQ(StringReplace("Hello World", "x", "y", false), "Hello World");
}