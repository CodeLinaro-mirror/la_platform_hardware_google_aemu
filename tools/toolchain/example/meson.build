project('hello-glib', 'cpp')

message = get_option('message')
py_gen = find_program('py_gen', native: true)

message_header = custom_target('message.h',
  output : 'message.h',
  command : [py_gen, '-m', message, '-o', '@OUTPUT@'],
  install : false)

glib_dep = dependency('glib-2.0')

executable('hello',
           'sample.cpp',
           sources: [message_header],
           dependencies : [glib_dep])
