#include <glib.h>
#include "message.h"

int main(int argc, char *argv[]) {
  g_print("%s\n", generated::msg.c_str());
  return 0;
}
